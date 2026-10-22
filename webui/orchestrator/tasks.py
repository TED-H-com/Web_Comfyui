from __future__ import annotations

import hashlib
import ast
import fcntl
import io
import json
import os
import queue
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import threading
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path

from .config import settings_from_env
from .cover_audit import (
    CANONICAL_COVER_MODE,
    COVER_AUTHORITY_SCHEMA,
    COVER_METADATA_SCHEMA,
    build_cover_audit,
    validate_cover_audit,
)
from .db import connect, stable_json, transaction
from .events import add_event
from .project_tasks import (
    project_qualification_enabled,
    run_project_contract_stage,
    run_project_pack_validate,
)
from .reel_native_stage import (
    EXTERNAL_IMPLEMENTATION_DEPENDENCIES as REEL_EXTERNAL_IMPLEMENTATION_DEPENDENCIES,
)
from .reel_native_stage import STAGE_BACKENDS as REEL_NATIVE_STAGE_BACKENDS
from .reel_native_stage import (
    STAGE_IMPLEMENTATION_DEPENDENCIES as REEL_NATIVE_STAGE_DEPENDENCIES,
)
from .reel_native_stage import (
    STAGE_RUNNER_CONTRACT_VERSIONS as REEL_NATIVE_STAGE_RUNNER_CONTRACTS,
)
from .reel_proxy_contract import (
    CANONICAL_REVIEW_PROXY_SETTINGS,
    PROXY_TASK_SCHEMA_VERSION,
    canonical_proxy_implementation_hash,
    canonical_proxy_input_hash,
)
from .reel_proxy_executor import execute_canonical_proxy
from .reel_qc_contract import (
    QC_JUDGE_AUTHORITY,
    QC_JUDGE_AUTHORITY_FINGERPRINT,
    QC_COST_ESTIMATE_VERSION,
    QC_DEFAULT_VISION_MODEL,
    QC_RELEASE_AUTHORITY_SCHEMA,
    QC_TASK_SCHEMA_VERSION,
    QC_QUALITY_AUTHORITY,
    QC_QUALITY_AUTHORITY_FINGERPRINT,
    QC_VISION_AUTHORITY,
    QC_VISION_AUTHORITY_FINGERPRINT,
    canonical_draft_completion,
    canonical_qc_environment_errors,
    canonical_qc_cost,
    canonical_json_sha256 as qc_contract_sha256,
    checks_verdict as reel_qc_checks_verdict,
    qc_evidence_input_hash,
    validate_quality_assessment,
)
from .store import adopt_artifact, artifact_abs_path


REEL_M4_WORKFLOW_VERSION = "m4-v12"


SUPPORTED_WORKFLOWS = {
    "mock.echo": {
        "version": "v1",
        "title": "Mock Echo",
        "task_type": "mock.echo",
        "lane": "cpu",
    },
    "ai_sister.reel_assemble_only": {
        "version": "v1",
        "title": "AI-Sister Reel Assemble Only",
        "task_type": "ai_sister.reel_assemble_only",
        "lane": "cpu",
    },
    "ai_sister.reel_pipeline": {
        "version": REEL_M4_WORKFLOW_VERSION,
        "title": "AI-Sister Reel Pipeline",
        "graph_builder": "reel_m4",
    },
    "mock.gpu_echo": {
        "version": "v1",
        "title": "Mock GPU Echo",
        "task_type": "mock.gpu_echo",
        "lane": "gpu",
    },
    "castle.voice_speak_guarded": {
        "version": "v1",
        "title": "Castle Guarded BreezyVoice",
        "task_type": "castle.voice_speak_guarded",
        "lane": "gpu",
    },
    "castle.reel_cover": {
        "version": "v1",
        "title": "Castle Reel Cover",
        "task_type": "castle.reel_cover",
        "lane": "cpu",
    },
    "castle.reel_assemble": {
        "version": "v1",
        "title": "Castle Reel Assemble",
        "task_type": "castle.reel_assemble",
        "lane": "cpu",
    },
    "castle.reel_qc": {
        "version": "v1",
        "title": "Castle Reel QC",
        "task_type": "castle.reel_qc",
        "lane": "cpu",
    },
    "ai_sister.render_sisters": {
        "version": "v2",
        "title": "AI-Sister Reel Render Sisters",
        "task_type": "ai_sister.render_sisters",
        "lane": "cpu",
        "max_attempts": 2,
    },
    "ai_sister.render_host": {
        "version": "v2",
        "title": "AI-Sister Reel Render Host",
        "task_type": "ai_sister.render_host",
        "lane": "cpu",
        "max_attempts": 2,
    },
    "castle.voxcpm_tts": {
        "version": "v1",
        "title": "Castle VoxCPM TTS",
        "task_type": "castle.voxcpm_tts",
        "lane": "gpu",
    },
    "castle.voxcpm_tts_batch": {
        "version": "v1",
        "title": "Castle VoxCPM TTS Batch",
        "task_type": "castle.voxcpm_tts_batch",
        "lane": "gpu",
    },
    "artifact.make_proxy": {
        "version": "v2",
        "title": "Artifact Review Proxy",
        "task_type": "artifact.make_proxy",
        "lane": "cpu",
    },
}

SUPPORTED_TASK_TYPES = {
    spec["task_type"]: {
        "lane": spec["lane"],
        "title": spec["title"],
        "version": spec["version"],
    }
    for spec in SUPPORTED_WORKFLOWS.values()
    if spec.get("task_type")
}
SUPPORTED_TASK_TYPES.update({
    "castle.project_pack_validate": {
        "lane": "cpu",
        "title": "Castle Project Pack Qualification",
        "version": "project-pack-qualification-v1",
    },
    "castle.project_contract_stage": {
        "lane": "cpu",
        "title": "Castle Project Contract Stage",
        "version": "project-contract-stage-v1",
    },
    "castle.reel_runtime_verify": {
        "lane": "cpu",
        "title": "Castle Reel Runtime Source Verify",
        "version": "reel-runtime-source-audit-v2_scene_decode_20260718",
    },
    "castle.reel_stage_cpu": {
        "lane": "cpu",
        "title": "Castle Reel Native CPU Stage",
        "version": "reel-native-stage-v5_pinned_scene_identity_20260718",
    },
    "castle.reel_stage_gpu": {
        "lane": "gpu",
        "title": "Castle Reel Native GPU Stage",
        "version": "reel-native-stage-v4_champion_audio_authority_20260717",
    },
    "castle.reel_prepare_render": {
        "lane": "cpu",
        "title": "Castle Reel Render Input Snapshot",
        "version": "reel-render-input-snapshot-v5_pinned_scene_identity_20260718",
    },
})

CUSTOM_DAG_WORKFLOW_TYPE = "custom.dag"
CUSTOM_DAG_VERSION = "v1"
WEBUI_ROOT = Path(__file__).resolve().parents[1]
SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}
REEL5_STAGES = [
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
    "render_sisters",
    "render_host",
    "cover",
    "assemble",
    "audio_listener_final",
    "qc",
]
REEL_NATIVE_CPU_STAGES = {
    "pull",
    "forum_meta",
    "script",
    "script_approved",
    "wardrobe_gate",
    "scene",
    "host_rig",
    "beats",
    "host_text",
}
REEL_NATIVE_GPU_STAGES = {
    "synth",
    "peer_synth",
    "host_reaction_bank",
    "host_synth",
    "champion_synth",
    "audio_listener_clips",
    "audio_listener_final",
}
REEL_NATIVE_STAGE_TASK_TYPES = {
    "castle.reel_stage_cpu": REEL_NATIVE_CPU_STAGES,
    "castle.reel_stage_gpu": REEL_NATIVE_GPU_STAGES,
}
REEL_NATIVE_BUNDLE_SCHEMA_VERSION = "castle-reel-stage-bundle-v1"
REEL_RENDER_SNAPSHOT_SCHEMA_VERSION = "castle-reel-render-snapshot-v1"
REEL_RUNTIME_GROUP_LOCK_SCHEMA_VERSION = "castle-reel-runtime-group-lock-v1"
REEL_RUNTIME_GROUPS = (
    "host_voice", "asr_models", "scene_assets", "sfx_sources",
    "sfx_outputs", "music",
)
REEL_NATIVE_RUNTIME_GROUPS = {
    "scene": ("scene_assets",),
    "synth": ("host_voice", "asr_models"),
    "peer_synth": ("host_voice", "asr_models"),
    "host_reaction_bank": ("host_voice", "asr_models"),
    "host_synth": ("host_voice", "asr_models"),
    "champion_synth": ("host_voice", "asr_models", "sfx_sources"),
}
REEL_CANONICAL_THEMES = {
    "tech", "finance", "politics", "education", "health", "environment",
    "law", "relationship", "family", "workplace", "science", "culture",
    "sports", "food", "travel", "psychology", "philosophy",
    "international", "media", "festival",
}
REEL_SFX_DEFAULTS = {
    "push_cue": "sfx_peer_push_crowd_cheer.wav",
    "boo_cue": "sfx_peer_boo_crowd_heckle.wav",
    "push_pop": "sfx_peer_push_pop.wav",
    "boo_pop": "sfx_peer_boo_pop.wav",
    "push_baby": "sfx_src/myinstants_kids_cheering.mp3",
    "boo_baby": "sfx_src/youtube_kids_booing_RVQtxeWzvcg.wav",
}
SECRET_ENV_RE = re.compile(r"(TOKEN|SECRET|KEY|PASSWORD)", re.IGNORECASE)
REEL_COVER_TASK_SCHEMA_VERSION = "4"
REEL_COVER_KEY_VISUAL_CACHE_SCHEMA = "castle-reel-cover-key-visual-cache-v1"
REEL_COVER_ART_POLICY_VERSION = "gpt-image-2-images-edit-1024x1536-high-v1"
REEL_COVER_POSTGEN_RETRY_TOKEN = "_cover_postgen_retry_input_hash"
REEL_COVER_POSTGEN_FAILURE_PREFIXES = (
    "cover recomposition authority invalid:",
    "canonical reel cover authority verifier rejected output",
    "canonical reel cover authority rejected output",
)
REEL_ASSEMBLE_TASK_SCHEMA_VERSION = "1"
REEL_QC_TASK_SCHEMA_VERSION = QC_TASK_SCHEMA_VERSION
REEL_RENDER_TASK_SCHEMA_VERSION = "3"
REEL_RENDER_INPUT_MANIFEST_SCHEMA_VERSION = "reel-render-input-manifest-v1"
REEL_RENDER_CHILD_WRAPPER_VERSION = "4_cpu_chromium_libx264_20260824"
REEL_RENDER_PEER_SFX_MODE = "pop_plus_long_baby_bed_noloop_v2"
RENDER_SHELL_ENV_KNOBS = (
    "AUD",
    "AUDIO_DIR",
    "BOO_BABY_SFX",
    "BOO_CUE_SFX",
    "BOO_POP_SFX",
    "CHAMPION_AUDIO_DIR",
    "CHAMPION_AUTHORITY_LOCK_FD",
    "CHAMPION_AUTHORITY_PREPARED",
    "CHAMPION_LINES",
    "CHAMPION_MANIFEST",
    "CHAMPION_RENDER_TAIL_PAD",
    "CHAMPION_STUB",
    "CHAMP_SEED",
    "CASTLE_EXPECTED_HOST_AUDIO_GENERATION",
    "CUTAWAYS",
    "CUT_DUR",
    "CUT_EVERY",
    "DEBATE_ID",
    "HL_THEME",
    "HOST_AUDIO_AUTHORITY_LOCK_FD",
    "HOST_AUDIO_DIR",
    "HOST_AUDIO_MANIFEST",
    "HOST_BEATS_AUTHORITY_LOCK_FD",
    "HOST_BOOKEND_TAIL_PAD",
    "HOST_PROOF_DIR",
    "MAIN_AUDIO_AUTHORITY_LOCK_FD",
    "MUSIC",
    "MUSIC_VOL",
    "OUT",
    "PEER_AUTHORITY_LOCK_FD",
    "PEER_BABY_DELAY_MS",
    "PEER_BABY_GAIN",
    "PEER_BABY_MIN_MS",
    "PEER_BABY_TAIL_MS",
    "PEER_BEATS",
    "PEER_CUE",
    "PEER_CUE_GAIN",
    "PEER_CUE_REPEAT_MS",
    "POP",
    "POP_GAIN",
    "POP_SFX",
    "PUSH_BABY_SFX",
    "PUSH_CUE_SFX",
    "PUSH_POP_SFX",
    "REACTIONS",
    "REACT_BEATS",
    "REEL_SUBDIR",
    "RENDER_CONCURRENCY",
    "RENDER_FPS",
    "RENDER_IDXS",
    "SCENE_THEME",
    "SKIP_AUDIO",
    "STICKER",
    "STINGER",
    "STINGER_LUFS",
    "TL",
    "TURNLIST",
    "VIDEO_CRF",
    "VIDEO_PRESET",
    "WIN_DUR",
    "XFADE",
)
RENDER_SISTERS_ENV_OVERLAY_KEYS = (
    "AUD",
    "BOO_BABY_SFX",
    "BOO_CUE_SFX",
    "BOO_POP_SFX",
    "HL_THEME",
    "MUSIC",
    "PEER_BEATS",
    "PUSH_BABY_SFX",
    "PUSH_CUE_SFX",
    "PUSH_POP_SFX",
    "REEL_SUBDIR",
    "RENDER_CONCURRENCY",
    "RENDER_FPS",
    "SKIP_AUDIO",
    "TL",
    "VIDEO_CRF",
    "VIDEO_PRESET",
    "XFADE",
)
RENDER_HOST_ENV_OVERLAY_KEYS = (
    "AUDIO_DIR",
    "CASTLE_EXPECTED_HOST_AUDIO_GENERATION",
    "CHAMPION_AUDIO_DIR",
    "CHAMPION_AUTHORITY_PREPARED",
    "CHAMPION_LINES",
    "CHAMPION_MANIFEST",
    "CHAMP_SEED",
    "DEBATE_ID",
    "HOST_AUDIO_DIR",
    "HOST_AUDIO_MANIFEST",
    "REACT_BEATS",
    "REACT_BEATS_REEL",
    "REEL_SUBDIR",
    "RENDER_CONCURRENCY",
    "RENDER_FPS",
    "SCENE_THEME",
    "TURNLIST",
    "TURNLIST_HOST",
    "TURNLIST_REEL",
    "VIDEO_CRF",
    "VIDEO_PRESET",
    "XFADE",
)
REEL_RENDER_OUTPUT_NAMES = {
    "render_sisters": "render_sisters_manifest",
    "sisters_formula": "sisters_formula",
    "render_host": "render_host_manifest",
    "host_formula": "host_formula",
    "host_intro": "host_intro",
    "host_outro": "host_outro",
    "host_demo": "host_demo",
    "sisters_segments": "sisters_segments",
    "host_segments": "host_segments",
}
REEL_COVER_COST_ESTIMATE_VERSION = "gpt-image-2-cover-v1"
REEL_QC_COST_ESTIMATE_VERSION = QC_COST_ESTIMATE_VERSION
REEL_QC_DEFAULT_VISION_MODEL = QC_DEFAULT_VISION_MODEL
REEL_QC_MAX_CHECKS = 200
REEL_QC_JUDGE_AUTHORITY = QC_JUDGE_AUTHORITY
REEL_QC_JUDGE_AUTHORITY_FINGERPRINT = QC_JUDGE_AUTHORITY_FINGERPRINT
REEL_QC_JUDGE_POLICY_VERSION = str(QC_JUDGE_AUTHORITY["policy_version"])
REEL_QC_JUDGE_POLICY = {
    key: value for key, value in QC_JUDGE_AUTHORITY.items()
    if key != "policy_version"
}
REEL_QC_VISION_AUTHORITY = QC_VISION_AUTHORITY
REEL_QC_VISION_AUTHORITY_FINGERPRINT = QC_VISION_AUTHORITY_FINGERPRINT
REEL_QC_QUALITY_AUTHORITY = QC_QUALITY_AUTHORITY
REEL_QC_QUALITY_AUTHORITY_FINGERPRINT = QC_QUALITY_AUTHORITY_FINGERPRINT
REEL_QC_BILLING_MARKERS = (
    "http error 401",
    "http error 403",
    "http error 429",
    "api key",
    "apikey",
    "permission_denied",
    "resource_exhausted",
    "quota",
    "billing",
)
REEL_COVER_MODEL_OVERRIDE_FIELDS = {"model", "model_override", "provider_model", "provider", "image_model", "engine"}
REEL_COVER_BILLING_MARKERS = (
    "COVER_BILLING_BLOCK",
    "insufficient_quota",
    "billing_hard_limit",
    "billing",
    "quota",
    "payment",
    "failed after retries: HTTP 429",
    "HTTP 402",
    "HTTP 401",
    "401 auth",
)
REEL_SCRIPT_PROVIDER_OUTAGE_MARKERS = (
    "script_provider_outage:",
    "scriptjudgeoutageerror",
    "single pinned openai script judge request failed",
    "single pinned script judge request failed",
    "script judge request failed",
)
REEL_SYNTH_SENTENCE_HARD_FAIL_MARKER = "synth_sentence_hard_fail:"
LOCAL_PATH_RE = re.compile(r"(/home/[^\s<>'\"]+|/tmp/[^\s<>'\"]+)")
URL_RE = re.compile(r"https?://[^\s<>'\"]+")
TASK_BINDING_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,79}")
TASK_BINDING_TOKEN_RE = re.compile(r"[A-Za-z0-9._-]{1,80}")
TASK_BINDING_SELECTORS = {
    "artifact_ref", "artifact_id", "content_hash", "json_value", "path_ref",
}


def input_hash(data) -> str:
    return hashlib.sha256(stable_json(data).encode("utf-8")).hexdigest()


def task_type_version(task_type: str) -> str:
    spec = SUPPORTED_TASK_TYPES.get(str(task_type or ""))
    if not spec:
        raise ValueError(f"Unsupported task type: {task_type}")
    return str(spec.get("version") or "v1")


def reel_script_provider_outage(task: dict, error: str) -> bool:
    """Classify an exhausted script-provider transaction, never prose/QC.

    This deliberately requires the canonical native ``script`` stage in
    addition to a narrow machine/error marker.  A generic timeout elsewhere in
    the Reel must not be promoted into this terminal policy.
    """
    if str(task.get("task_type") or "") != "castle.reel_stage_cpu":
        return False
    try:
        payload = json.loads(task.get("input_json") or "{}")
    except (TypeError, ValueError):
        return False
    if not isinstance(payload, dict) or payload.get("stage") != "script":
        return False
    lowered = str(error or "").lower()
    return any(marker in lowered for marker in REEL_SCRIPT_PROVIDER_OUTAGE_MARKERS)


def reel_synth_sentence_hard_fail(task: dict, error: str) -> bool:
    """Classify only the verified native main-synth sentence rejection route."""
    if str(task.get("task_type") or "") not in {
        "castle.reel_stage_cpu", "castle.reel_stage_gpu",
    }:
        return False
    try:
        payload = json.loads(task.get("input_json") or "{}")
    except (TypeError, ValueError):
        return False
    if not isinstance(payload, dict) or payload.get("stage") != "synth":
        return False
    return REEL_SYNTH_SENTENCE_HARD_FAIL_MARKER in str(error or "")


def reel_synth_sentence_hard_fail_data(error: str) -> dict:
    text = str(error or "")
    if REEL_SYNTH_SENTENCE_HARD_FAIL_MARKER not in text:
        return {}
    raw = text.split(REEL_SYNTH_SENTENCE_HARD_FAIL_MARKER, 1)[1].strip()
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _semantic_task_input(value):
    """Remove storage identity while retaining input content and literal policy.

    Artifact ids and local paths are transport details.  M4 reuse is keyed by
    node implementation version plus content hashes, so the same bytes under a
    newly cloned artifact id remain reusable.
    """
    if isinstance(value, dict):
        kind = str(value.get("kind") or "")
        content_hash = str(value.get("content_hash") or "")
        if kind in {"artifact", "path"} and content_hash:
            return {"kind": kind, "content_hash": content_hash}
        transport_ids = set()
        for key in value:
            text = str(key)
            if not text.endswith("_artifact_id"):
                continue
            prefix = text[: -len("_artifact_id")]
            if f"{prefix}_content_hash" in value:
                transport_ids.add(text)
        return {
            str(key): _semantic_task_input(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if not str(key).startswith("_") and str(key) not in transport_ids
        }
    if isinstance(value, list):
        return [_semantic_task_input(item) for item in value]
    return value


def task_input_signature(task_type: str, payload: dict) -> str:
    return input_hash({
        "task_type": task_type,
        "task_version": task_type_version(task_type),
        "input": _semantic_task_input(payload),
    })


def normalize_task_bindings(raw, *, task_key: str) -> dict:
    if raw in (None, {}):
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"custom.dag task {task_key} bindings must be an object")
    normalized = {}
    for field, value in raw.items():
        field = str(field or "").strip()
        if not TASK_BINDING_NAME_RE.fullmatch(field):
            raise ValueError(f"custom.dag task {task_key} has unsafe binding field: {field}")
        if not isinstance(value, dict):
            raise ValueError(f"custom.dag task {task_key} binding {field} must be an object")
        allowed = {"task", "output", "select", "path", "pointer"}
        unknown = sorted(key for key in value if key not in allowed)
        if unknown:
            raise ValueError(
                f"custom.dag task {task_key} binding {field} unknown fields: {', '.join(unknown)}")
        producer = str(value.get("task") or "").strip()
        output = str(value.get("output") or "").strip()
        selector = str(value.get("select") or "artifact_ref").strip()
        if not TASK_BINDING_TOKEN_RE.fullmatch(producer):
            raise ValueError(f"custom.dag task {task_key} binding {field} has unsafe task")
        if not TASK_BINDING_TOKEN_RE.fullmatch(output):
            raise ValueError(f"custom.dag task {task_key} binding {field} has unsafe output")
        if selector not in TASK_BINDING_SELECTORS:
            raise ValueError(
                f"custom.dag task {task_key} binding {field} select must be one of: "
                f"{', '.join(sorted(TASK_BINDING_SELECTORS))}")
        item = {"task": producer, "output": output, "select": selector}
        if selector == "path_ref":
            path = str(value.get("path") or "").strip()
            if not path or not Path(path).expanduser().is_absolute():
                raise ValueError(
                    f"custom.dag task {task_key} binding {field} path_ref requires an absolute path")
            item["path"] = path
        elif selector == "json_value":
            pointer = str(value.get("pointer") or "").strip()
            if (not pointer.startswith("/") or len(pointer) > 240
                    or any(part in {"", ".", ".."} for part in pointer.split("/")[1:])):
                raise ValueError(
                    f"custom.dag task {task_key} binding {field} json_value requires a safe JSON pointer")
            item["pointer"] = pointer
            if "path" in value:
                raise ValueError(
                    f"custom.dag task {task_key} binding {field} path is only valid for path_ref")
        elif "path" in value:
            raise ValueError(
                f"custom.dag task {task_key} binding {field} path is only valid for path_ref")
        elif "pointer" in value:
            raise ValueError(
                f"custom.dag task {task_key} binding {field} pointer is only valid for json_value")
        normalized[field] = item
    return normalized


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def workflow_topology_graph(graph: dict) -> dict:
    tasks = []
    for task in graph.get("tasks") or []:
        bindings = {}
        for field, binding in sorted((task.get("bindings") or {}).items()):
            bindings[field] = {
                key: value
                for key, value in binding.items()
                if key in {"task", "output", "select", "pointer"}
            }
        tasks.append({
            "key": task.get("key"),
            "task_type": task.get("task_type"),
            "lane": task.get("lane"),
            "deps": list(task.get("deps") or []),
            "bindings": bindings,
            "max_attempts": task.get("max_attempts", 1),
            "reuse_policy": task.get("reuse_policy", "off"),
        })
    return {
        "workflow_type": graph.get("workflow_type"),
        "version": graph.get("version"),
        "tasks": tasks,
    }


def workflow_version_id(workflow_type: str, graph: dict | None = None) -> str:
    if workflow_type == CUSTOM_DAG_WORKFLOW_TYPE and graph is not None:
        digest = input_hash(workflow_topology_graph(graph))[:16]
        return f"{workflow_type}:{CUSTOM_DAG_VERSION}:{digest}"
    if workflow_type not in SUPPORTED_WORKFLOWS:
        raise ValueError(f"Unsupported workflow type: {workflow_type}")
    return f"{workflow_type}:{SUPPORTED_WORKFLOWS[workflow_type]['version']}"


def _local_python_dependency_paths(source_root: Path, seeds: list[Path]) -> list[Path]:
    """Return local Python imports reachable from seed scripts.

    Reel backends are scripts rather than a packaged wheel, so a direct script
    hash misses policy modules imported beside it.  This closure deliberately
    follows only regular ``.py`` files under the configured source root.
    """
    root = source_root.resolve()
    pending = list(seeds)
    seen: set[Path] = set()
    while pending:
        path = pending.pop()
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError):
            continue
        if resolved in seen or resolved.suffix != ".py" or resolved.is_symlink():
            continue
        seen.add(resolved)
        try:
            tree = ast.parse(resolved.read_text(encoding="utf-8"), filename=str(resolved))
        except (OSError, SyntaxError, UnicodeError) as error:
            raise ValueError(f"cannot parse Reel Python implementation: {resolved.name}") from error
        modules: set[str] = set()
        relative_parent = resolved.parent.relative_to(root)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = str(node.module or "")
                if node.level:
                    parent_parts = list(relative_parent.parts)
                    trim = max(0, node.level - 1)
                    if trim:
                        parent_parts = parent_parts[:-trim]
                    prefix = ".".join(parent_parts)
                    module = ".".join(part for part in (prefix, module) if part)
                if module:
                    modules.add(module)
        for module in modules:
            relative = Path(*module.split("."))
            candidates = (root / relative).with_suffix(".py"), root / relative / "__init__.py"
            for candidate in candidates:
                if candidate.is_file() and not candidate.is_symlink() and candidate.resolve() not in seen:
                    pending.append(candidate)
                    break
    return sorted(seen, key=lambda item: item.relative_to(root).as_posix())


def _implementation_file_records(source_root: Path, paths: list[tuple[str, Path]]) -> list[dict]:
    root = source_root.resolve()
    records: dict[str, dict] = {}
    python_seeds = []
    for label, path in paths:
        unresolved = path.expanduser().absolute()
        if unresolved.is_symlink() or not unresolved.is_file():
            raise ValueError(f"Reel implementation is missing or unsafe: {unresolved.name}")
        resolved = unresolved.resolve()
        try:
            relative = resolved.relative_to(root).as_posix()
            key = f"source:{relative}"
            if resolved.suffix == ".py":
                python_seeds.append(resolved)
        except ValueError:
            key = f"external:{label}:{resolved.name}"
        records[key] = {"path": key, "sha256": _file_sha256(resolved)}
    for dependency in _local_python_dependency_paths(root, python_seeds):
        relative = dependency.relative_to(root).as_posix()
        key = f"source:{relative}"
        records[key] = {"path": key, "sha256": _file_sha256(dependency)}
    return [records[key] for key in sorted(records)]


def _reel_optional_source_file_record(source_root: Path, relative: str) -> dict:
    root = source_root.resolve()
    path = root / relative
    if path.is_symlink():
        raise ValueError(f"Reel source input is unsafe: {relative}")
    if not path.exists():
        return {"path": relative, "exists": False, "sha256": ""}
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ValueError(f"Reel source input escapes source root: {relative}") from error
    if not resolved.is_file():
        raise ValueError(f"Reel source input is not a regular file: {relative}")
    return {
        "path": relative,
        "exists": True,
        "sha256": _file_sha256(resolved),
    }


def _reel_optional_source_glob_records(
    source_root: Path,
    patterns: list[str] | tuple[str, ...],
) -> list[dict]:
    root = source_root.resolve()
    records: list[dict] = []
    seen: set[str] = set()
    for pattern in patterns:
        for path in sorted(root.glob(pattern), key=lambda item: item.as_posix()):
            if path.is_symlink():
                raise ValueError(f"Reel source input is unsafe: {path.name}")
            resolved = path.resolve()
            try:
                relative = resolved.relative_to(root).as_posix()
            except ValueError as error:
                raise ValueError(
                    f"Reel source input escapes source root: {path}"
                ) from error
            if relative in seen:
                continue
            if not resolved.is_file():
                raise ValueError(
                    f"Reel source input is not a regular file: {relative}")
            records.append({
                "path": relative,
                "exists": True,
                "sha256": _file_sha256(resolved),
            })
            seen.add(relative)
    return records


def _reel_qc_host_reaction_report_records(
    source_root: Path,
    episode: str,
) -> list[dict]:
    records = _reel_optional_source_glob_records(
        source_root,
        (
            f"qc_report_{episode}.json",
            f".castle-native/{episode}/qc-output/qc_report_{episode}*.json",
        ),
    )
    filtered: list[dict] = []
    for record in records:
        path = source_root / str(record["path"])
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            filtered.append(record)
            continue
        if isinstance(payload, dict) and "release_authority" in payload:
            continue
        filtered.append(record)
    return filtered


def _reel_voice_reference_records(source_root: Path) -> list[dict]:
    voice_lab = source_root.resolve().parent
    refs_path = voice_lab / "refs.json"
    if refs_path.is_symlink() or not refs_path.is_file():
        raise ValueError("Reel voice reference registry is missing or unsafe")
    try:
        value = json.loads(refs_path.read_text(encoding="utf-8"))
    except Exception as error:
        raise ValueError("Reel voice reference registry is invalid") from error
    refs = value.get("refs") if isinstance(value, dict) else None
    if not isinstance(refs, dict) or not refs:
        raise ValueError("Reel voice reference registry has no refs")
    records = [{"path": "voice_refs:refs.json", "sha256": _file_sha256(refs_path)}]
    seen = set()
    for persona, row in sorted(refs.items()):
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", str(persona)) or not isinstance(row, dict):
            raise ValueError("Reel voice reference registry contains an unsafe persona")
        relative = _safe_bundle_relative(row.get("wav"), "voice reference wav")
        candidate = voice_lab.joinpath(*Path(relative).parts)
        if candidate.is_symlink():
            raise ValueError("Reel voice reference wav is a symlink")
        try:
            path = candidate.resolve(strict=True)
            path.relative_to(voice_lab)
        except (OSError, ValueError) as error:
            raise ValueError("Reel voice reference wav escapes voice-lab") from error
        if not path.is_file():
            raise ValueError("Reel voice reference wav is missing")
        key = path.relative_to(voice_lab).as_posix()
        if key in seen:
            continue
        seen.add(key)
        records.append({"path": f"voice_refs:{key}", "sha256": _file_sha256(path)})
    return records


def _reel_breezyvoice_engine_records(source_root: Path) -> list[dict]:
    engine_root = source_root.resolve().parent / "BreezyVoice"
    entry = engine_root / "single_inference.py"
    if engine_root.is_symlink() or not engine_root.is_dir() or entry.is_symlink() or not entry.is_file():
        raise ValueError("BreezyVoice inference engine is missing or unsafe")
    # single_inference loads HyperPyYAML objects and G2PW dynamically.  An AST
    # import walk alone therefore misses code that can alter generated audio.
    # Pin the complete allowlisted local implementation trees and their data
    # configs, plus the installed G2PW package that owns the ONNX loader.
    engine_files = {entry.resolve()}
    for relative in ("cosyvoice", "third_party/Matcha-TTS"):
        tree = engine_root / relative
        if tree.is_symlink() or not tree.is_dir():
            raise ValueError(f"BreezyVoice implementation tree is missing or unsafe: {relative}")
        selected = [
            path for path in tree.rglob("*")
            if path.suffix.lower() in {".py", ".yaml", ".yml", ".json"}
        ]
        if not selected:
            raise ValueError(f"BreezyVoice implementation tree is empty: {relative}")
        for path in selected:
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"BreezyVoice implementation file is unsafe: {path}")
            resolved = path.resolve()
            try:
                resolved.relative_to(engine_root)
            except ValueError as error:
                raise ValueError("BreezyVoice implementation file escapes its root") from error
            engine_files.add(resolved)

    records = [{
        "path": f"breezyvoice_engine:{path.relative_to(engine_root).as_posix()}",
        "sha256": _file_sha256(path),
    } for path in sorted(engine_files, key=lambda item: item.relative_to(engine_root).as_posix())]

    voice_lab = source_root.resolve().parent
    g2pw_roots = [
        path for path in (voice_lab / ".venv/lib").glob("python*/site-packages/g2pw")
        if path.is_dir() and not path.is_symlink()
    ]
    if len(g2pw_roots) != 1:
        raise ValueError("expected exactly one safe Reel G2PW package")
    g2pw_root = g2pw_roots[0].resolve()
    g2pw_files = sorted(
        (
            path for path in g2pw_root.rglob("*")
            if path.is_file() and path.suffix.lower() in {".py", ".json"}
        ),
        key=lambda item: item.relative_to(g2pw_root).as_posix(),
    )
    if not g2pw_files:
        raise ValueError("Reel G2PW package has no Python implementation files")
    if not any(
            path.relative_to(g2pw_root).as_posix()
            == "char_bopomofo_dict.json" for path in g2pw_files):
        raise ValueError("Reel G2PW static pronunciation lexicon is missing")
    for path in g2pw_files:
        if path.is_symlink() or not path.is_file():
            raise ValueError("Reel G2PW implementation file is unsafe")
        records.append({
            "path": f"g2pw_package:{path.relative_to(g2pw_root).as_posix()}",
            "sha256": _file_sha256(path),
        })
    return records


def _reel_whisper_implementation_records(source_root: Path) -> list[dict]:
    """Pin the installed OpenAI Whisper code, data files, and package version."""
    voice_lab = source_root.resolve().parent
    site_packages = [
        path for path in (voice_lab / ".venv/lib").glob("python*/site-packages")
        if path.is_dir() and not path.is_symlink()
    ]
    if len(site_packages) != 1:
        raise ValueError("expected exactly one safe Reel Python site-packages directory")
    site = site_packages[0].resolve()
    package = site / "whisper"
    if package.is_symlink() or not package.is_dir():
        raise ValueError("installed Reel Whisper package is missing or unsafe")
    allowed_suffixes = {".json", ".npz", ".py", ".tiktoken"}
    files = sorted(
        (
            path for path in package.rglob("*")
            if path.is_file()
            and not path.is_symlink()
            and "__pycache__" not in path.parts
            and path.suffix.lower() in allowed_suffixes
        ),
        key=lambda item: item.relative_to(package).as_posix(),
    )
    if not files:
        raise ValueError("installed Reel Whisper package has no implementation files")
    records = [{
        "path": f"whisper_package:{path.relative_to(package).as_posix()}",
        "sha256": _file_sha256(path),
    } for path in files]

    metadata_files = sorted(site.glob("openai_whisper-*.dist-info/METADATA"))
    if len(metadata_files) != 1:
        raise ValueError("expected exactly one installed openai-whisper distribution")
    metadata = metadata_files[0]
    if metadata.is_symlink() or not metadata.is_file():
        raise ValueError("installed openai-whisper metadata is unsafe")
    name = ""
    version = ""
    for line in metadata.read_text(encoding="utf-8").splitlines():
        if line.startswith("Name:") and not name:
            name = line.split(":", 1)[1].strip()
        elif line.startswith("Version:") and not version:
            version = line.split(":", 1)[1].strip()
        if name and version:
            break
    if name.lower() != "openai-whisper" or not re.fullmatch(r"[A-Za-z0-9._+-]{1,80}", version):
        raise ValueError("installed openai-whisper name/version is invalid")
    records.append({
        "path": f"whisper_distribution:openai-whisper=={version}",
        "sha256": _file_sha256(metadata),
    })
    return records


def _reel_policy_data_records(source_root: Path) -> list[dict]:
    path = source_root / "forum_title_overrides.json"
    if path.is_symlink() or not path.is_file():
        raise ValueError("Reel forum title policy data is missing or unsafe")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        raise ValueError("Reel forum title policy data is invalid") from error
    if not isinstance(value, dict):
        raise ValueError("Reel forum title policy data must be an object")
    return [{"path": "policy_data:forum_title_overrides.json", "sha256": _file_sha256(path)}]


def _reel_pose_asset_records(source_root: Path) -> list[dict]:
    pose_root = source_root / "tachie/poses/react"
    if pose_root.is_symlink() or not pose_root.is_dir():
        raise ValueError("Reel reaction pose root is missing or unsafe")
    poses = sorted(pose_root.glob("*.png"), key=lambda item: item.name)
    if not poses:
        raise ValueError("Reel reaction pose root has no PNG assets")
    records = []
    for path in poses:
        if path.is_symlink() or not path.is_file():
            raise ValueError("Reel reaction pose asset is missing or unsafe")
        records.append({
            "path": f"reaction_pose:{path.name}",
            "sha256": _file_sha256(path),
        })
    return records


def _reel_native_implementation_hash(
    source_root: Path,
    stage: str,
    *,
    record_cache: dict[tuple[str, str], tuple[dict, ...]] | None = None,
) -> str:
    declared = REEL_NATIVE_STAGE_BACKENDS.get(stage)
    if not declared:
        raise ValueError(f"native Reel stage has no backend registry entry: {stage}")
    runner_contract = REEL_NATIVE_STAGE_RUNNER_CONTRACTS.get(stage)
    if not runner_contract:
        raise ValueError(f"native Reel stage has no runner contract: {stage}")
    declared_files: list[tuple[str, Path]] = [
        ("CASTLE_REEL_NATIVE_ORCHESTRATOR", Path(__file__)),
    ]
    routes = []
    for relative in declared:
        if str(relative).startswith(("ssh:", "derived:")):
            routes.append(str(relative))
            continue
        declared_files.append((str(relative), source_root / str(relative)))
    dependencies = REEL_NATIVE_STAGE_DEPENDENCIES.get(stage, ())
    declared_files.extend(
        (f"dependency:{relative}", source_root / relative)
        for relative in dependencies
    )
    cache_root = str(source_root.resolve())

    def shared_records(label: str, producer) -> list[dict]:
        if record_cache is None:
            return producer()
        key = (cache_root, label)
        records = record_cache.get(key)
        if records is None:
            records = tuple(dict(row) for row in producer())
            record_cache[key] = records
        return [dict(row) for row in records]

    files = []
    interpreter_link = Path.home() / ".local/share/web-comfyui/integrations/voice/.venv/bin/python"
    try:
        interpreter = interpreter_link.resolve(strict=True)
    except OSError as error:
        raise ValueError("canonical Reel Python interpreter is missing") from error
    if not interpreter.is_file() or not os.access(interpreter, os.X_OK):
        raise ValueError("canonical Reel Python interpreter is unavailable")
    files.extend(shared_records(
        "canonical-python",
        lambda: [{
            "path": "runtime:canonical-python",
            "sha256": _file_sha256(interpreter),
        }],
    ))
    files.extend(_implementation_file_records(source_root, declared_files))
    if stage in {"synth", "peer_synth", "host_reaction_bank", "champion_synth"}:
        files.extend(shared_records(
            "voice-references",
            lambda: _reel_voice_reference_records(source_root),
        ))
        files.extend(shared_records(
            "breezyvoice-engine",
            lambda: _reel_breezyvoice_engine_records(source_root),
        ))
    elif stage == "host_synth":
        files.extend(shared_records(
            "breezyvoice-engine",
            lambda: _reel_breezyvoice_engine_records(source_root),
        ))
    if stage in {
        "synth", "peer_synth", "host_reaction_bank", "host_synth",
        "champion_synth",
    }:
        files.extend(shared_records(
            "whisper-implementation",
            lambda: _reel_whisper_implementation_records(source_root),
        ))
    if stage in {"script", "host_text"}:
        files.extend(shared_records(
            "policy-data",
            lambda: _reel_policy_data_records(source_root),
        ))
    return input_hash({
        "runner_contract_version": runner_contract,
        "stage": stage,
        "files": files,
        "routes": routes,
        "declared_dependencies": list(dependencies),
    })


def _reel_m4_external_implementation_hash(source_root: Path, task: str) -> str:
    defaults = {
        "cover": [
            ("CASTLE_REEL_COVER_SCRIPT", source_root / "gen_cover5.py"),
            ("CASTLE_REEL_COVER_ORCHESTRATOR", Path(__file__)),
            (
                "CASTLE_REEL_COVER_PYTHON",
                Path.home() / ".local/share/web-comfyui/integrations/voice/.venv/bin/python",
            ),
        ],
        "render_sisters": [
            ("CASTLE_REEL_RENDER_HIGHLIGHT_SCRIPT", source_root / "render_highlight.sh"),
            ("CASTLE_REEL_RENDER_SEEK_LIB", source_root / "render_seek_lib.sh"),
            ("CASTLE_REEL_RENDER_BUILD_PUPPET_SCRIPT", source_root / "build_puppet.py"),
            ("CASTLE_REEL_RENDER_REACT_TIMING_SCRIPT", source_root / "react_timing.py"),
            ("CASTLE_REEL_RENDER_BUILD_HIGHLIGHT_SCRIPT", source_root / "build_highlight.py"),
            ("CASTLE_REEL_RENDER_GEN_REACTIONS_SCRIPT", source_root / "gen_reactions.py"),
            ("CASTLE_REEL_RENDER_MIX_REACTIONS_SCRIPT", source_root / "mix_reactions.py"),
            ("CASTLE_REEL_RENDER_AUTHORITY_LOCK_SCRIPT", source_root / "authority_lock_capability.py"),
            ("CASTLE_REEL_RENDER_ASSEMBLE_XFADE_SCRIPT", source_root / "assemble_xfade.py"),
            ("CASTLE_REEL_SEEK_RENDER_SCRIPT", source_root.parent / "research-repos/huashu-design/scripts/render-video-seek.js"),
            ("CASTLE_REEL_BROWSER_ISOLATION_SCRIPT", source_root.parent / "research-repos/huashu-design/scripts/reel-browser-isolation.js"),
        ],
        "render_host": [
            ("CASTLE_REEL_RENDER_HOST_SCRIPT", source_root / "render_host.sh"),
            ("CASTLE_REEL_RENDER_SEEK_LIB", source_root / "render_seek_lib.sh"),
            ("CASTLE_REEL_RENDER_BUILD_PUPPET_SCRIPT", source_root / "build_puppet.py"),
            ("CASTLE_REEL_RENDER_MUX_CHAMPION_SCRIPT", source_root / "mux_champion.py"),
            ("CASTLE_REEL_RENDER_REACT_TIMING_SCRIPT", source_root / "react_timing.py"),
            ("CASTLE_REEL_RENDER_ASSEMBLE_XFADE_SCRIPT", source_root / "assemble_xfade.py"),
            ("CASTLE_REEL_RENDER_CHAMPION_AUTHORITY_SCRIPT", source_root / "champion_authority.py"),
            ("CASTLE_REEL_RENDER_AUDIO_READY_AUTHORITY_SCRIPT", source_root / "audio_ready_authority.py"),
            ("CASTLE_REEL_RENDER_HOST_AUDIO_AUTHORITY_SCRIPT", source_root / "host_audio_authority.py"),
            ("CASTLE_REEL_RENDER_HOST_RIG_SCRIPT", source_root / "host_doll_to_parts.py"),
            ("CASTLE_REEL_RENDER_AUTHORITY_LOCK_SCRIPT", source_root / "authority_lock_capability.py"),
            ("CASTLE_REEL_SEEK_RENDER_SCRIPT", source_root.parent / "research-repos/huashu-design/scripts/render-video-seek.js"),
            ("CASTLE_REEL_BROWSER_ISOLATION_SCRIPT", source_root.parent / "research-repos/huashu-design/scripts/reel-browser-isolation.js"),
        ],
        "assemble": [
            ("CASTLE_REEL_ASSEMBLE_SCRIPT", source_root / "assemble_reel5.sh"),
            ("CASTLE_REEL_XFADE_SCRIPT", source_root / "assemble_xfade.py"),
            ("CASTLE_REEL_MUSIC_POLICY_SCRIPT", source_root / "music_policy.py"),
        ],
        "qc": [
            ("CASTLE_REEL_QC_SCRIPT", source_root / "qc_reel5.sh"),
            ("CASTLE_REEL_QC_PY", source_root / "qc_reel5.py"),
            (
                "CASTLE_REEL_QC_WARDROBE_INTEGRITY",
                source_root / "wardrobe_integrity.py",
            ),
            (
                "CASTLE_REEL_QC_ORCHESTRATOR",
                Path(__file__),
            ),
            (
                "CASTLE_REEL_QC_CONTRACT",
                Path(__file__).with_name("reel_qc_contract.py"),
            ),
        ],
    }
    declared = defaults.get(task)
    if declared is None:
        raise ValueError(f"unknown Reel M4 external implementation: {task}")
    implementation_paths = []
    for env_name, default in declared:
        path = Path(os.environ.get(env_name, str(default))).expanduser().absolute()
        implementation_paths.append((env_name, path))
    dependencies = REEL_EXTERNAL_IMPLEMENTATION_DEPENDENCIES.get(task, ())
    implementation_paths.extend(
        (f"dependency:{relative}", source_root / relative)
        for relative in dependencies
    )
    cover_python = None
    hashable_paths = implementation_paths
    if task == "cover":
        cover_python = next(
            path for label, path in implementation_paths
            if label == "CASTLE_REEL_COVER_PYTHON"
        )
        # A venv launcher is intentionally a symlink: executing its resolved
        # target directly would lose that venv's site-packages.  Keep it out of
        # the generic no-symlink implementation walker and bind it explicitly.
        hashable_paths = [
            (label, path) for label, path in implementation_paths
            if label != "CASTLE_REEL_COVER_PYTHON"
        ]
    files = _implementation_file_records(source_root, hashable_paths)
    if task == "cover":
        assert cover_python is not None
        files.append(_reel_cover_python_launcher_record(cover_python))
        files.append(_reel_cover_runtime_record(source_root, cover_python))
    if task == "qc":
        files.extend(_reel_whisper_implementation_records(source_root))
    if task in {"cover", "render_sisters", "render_host", "qc"}:
        files.extend(_reel_policy_data_records(source_root))
    if task in {"render_sisters", "render_host"}:
        files.extend(_reel_pose_asset_records(source_root))
        avatar_root = Path(os.environ.get(
            "CASTLE_REEL_WEB_AVATARS_ROOT",
            Path.home() / ".local/share/web-comfyui/integrations/characters/public/avatars",
        )).expanduser().absolute()
        if avatar_root.is_symlink() or not avatar_root.is_dir():
            raise ValueError("Reel web avatar root is missing or unsafe")
        avatars = sorted(avatar_root.glob("*.png"), key=lambda item: item.name)
        if not avatars:
            raise ValueError("Reel web avatar root has no PNG assets")
        for avatar in avatars:
            if avatar.is_symlink() or not avatar.is_file():
                raise ValueError("Reel web avatar asset is missing or unsafe")
            files.append({
                "path": f"web_avatar:{avatar.name}",
                "sha256": _file_sha256(avatar),
            })
    return input_hash({
        "task": task,
        "files": files,
        "declared_dependencies": list(dependencies),
    })


def _reel_m4_binding(
    task: str,
    output: str,
    *,
    select: str = "artifact_ref",
    path: str | None = None,
    pointer: str | None = None,
) -> dict:
    binding = {"task": task, "output": output, "select": select}
    if path is not None:
        binding["path"] = path
    if pointer is not None:
        binding["pointer"] = pointer
    return binding


def _reel_m4_task(
    key: str,
    task_type: str,
    task_input: dict,
    *,
    deps: list[str] | tuple[str, ...] = (),
    bindings: dict | None = None,
    reuse: bool = True,
    max_attempts: int = 1,
) -> dict:
    spec = SUPPORTED_TASK_TYPES[task_type]
    return {
        "key": key,
        "task_type": task_type,
        "lane": spec["lane"],
        "deps": list(deps),
        "bindings": dict(bindings or {}),
        "input": dict(task_input),
        "max_attempts": max_attempts,
        "reuse_policy": "input_hash" if reuse else "off",
    }


def _reel_native_stage_declared_input(
    source_root: Path,
    stage: str,
    episode: str,
) -> dict:
    declared: dict = {}
    dependencies = REEL_NATIVE_STAGE_DEPENDENCIES.get(stage, ())
    if "listener_rejection_ledger.py" in dependencies:
        declared["listener_rejection_ledger"] = _reel_optional_source_file_record(
            source_root,
            f"listener_rejection_ledger_{episode}.json",
        )
    if stage in {"host_text", "audio_listener_clips"}:
        declared["host_reaction_bank_rejections"] = (
            _reel_optional_source_file_record(
                source_root, "host_reaction_bank_rejections.json")
        )
        declared["qc_host_reaction_reports"] = (
            _reel_qc_host_reaction_report_records(source_root, episode)
        )
    return declared


def reel_m4_graph(payload: dict) -> dict:
    """Return Castle's canonical, single-episode Reel M4 task graph.

    The public workflow type stays stable for review/UI compatibility.  It no
    longer creates the legacy opaque pipeline task, and no node may invoke the
    old pipeline driver.
    """
    episode = _episode_id(payload)
    if _reel_bool(payload.get("dry_run") if "dry_run" in payload else payload.get("dryRun")):
        raise ValueError("ai_sister.reel_pipeline M4 does not publish synthetic dry-run success")
    if _reel_from_stage(payload) or _reel_force_stages(payload) or _reel_bool(payload.get("force")):
        raise ValueError("ai_sister.reel_pipeline M4 reruns use the failed task retry API")

    source_root = Path(os.environ.get(
        "CASTLE_REEL5_SOURCE_ROOT",
        Path.home() / ".local/share/web-comfyui/integrations/voice/video-mvp",
    )).expanduser().resolve()
    native_root = source_root / ".castle-native" / episode
    render_input_root = native_root / "render-inputs"
    render_output_root = native_root / "render-outputs"
    cover_output_root = native_root / "cover-output"
    qc_output_root = native_root / "qc-output"
    reel_root = source_root / f"reel{episode}"
    rough = str(payload.get("preset") or "").strip() == "rough-cut"
    approved_script = payload.get("approved_script")
    if approved_script is not None:
        approved_script = _reel_ref_payload(
            approved_script, "approved_script")
    review_only = approved_script is not None
    main_audio_resynth_idxs = _reel_main_audio_resynth_idxs(payload)
    if main_audio_resynth_idxs and not review_only:
        raise ValueError("main_audio_resynth_idxs requires approved_script recovery")
    main_audio_cache_revision = _reel_main_audio_cache_revision(
        payload, required=bool(main_audio_resynth_idxs))
    if main_audio_cache_revision and not main_audio_resynth_idxs:
        raise ValueError("main_audio_cache_revision requires main_audio_resynth_idxs")
    metadata_task = "script" if review_only else "forum_meta"
    source_literal = {
        "episode": episode,
        "source_root": str(source_root),
        "_orchestrator_reuse_authority": True,
    }
    implementation_record_cache: dict[
        tuple[str, str], tuple[dict, ...]
    ] = {}

    def native_implementation_hash(stage_name: str) -> str:
        return _reel_native_implementation_hash(
            source_root,
            stage_name,
            record_cache=implementation_record_cache,
        )

    def stage(
        key: str,
        stage_name: str,
        *,
        deps: list[str] | tuple[str, ...],
        bindings: dict | None = None,
        gpu: bool = False,
        reuse: bool = True,
        extra_input: dict | None = None,
    ) -> dict:
        return _reel_m4_task(
            key,
            "castle.reel_stage_gpu" if gpu else "castle.reel_stage_cpu",
            {
                **source_literal,
                "stage": stage_name,
                "implementation_hash": native_implementation_hash(stage_name),
                **_reel_native_stage_declared_input(
                    source_root, stage_name, episode),
                **dict(extra_input or {}),
            },
            deps=deps,
            bindings=bindings,
            reuse=reuse,
        )

    tasks = [
        _reel_m4_task(
            "runtime_assets",
            "castle.reel_runtime_verify",
            {
                "source_root": str(source_root),
                "_orchestrator_reuse_authority": True,
            },
            # Source health is temporal: every Reel run performs one deep,
            # read-only audit of the locked runtime bytes.  CAS/archive backup
            # remains an optional operator maintenance path, outside this DAG.
            # Receipt bytes stay deterministic, so healthy downstream nodes
            # can still reuse.
            reuse=False,
        ),
        *([] if review_only else [
            _reel_m4_task(
                "pull",
                "castle.reel_stage_cpu",
                {
                    **source_literal,
                    "stage": "pull",
                    "implementation_hash": native_implementation_hash("pull"),
                },
                deps=("runtime_assets",),
                reuse=False,
            ),
            _reel_m4_task(
                "forum_meta",
                "castle.reel_stage_cpu",
                {
                    **source_literal,
                    "stage": "forum_meta",
                    "implementation_hash": native_implementation_hash(
                        "forum_meta"),
                },
                deps=("runtime_assets", "pull"),
                bindings={
                    "input_01_pull": _reel_m4_binding(
                        "pull", "stage_bundle"),
                },
                reuse=False,
                # Pull already captured current post metadata and debate turns
                # in one atomic read. Derive locally so a redundant SSH
                # connection cannot strand normal generation before synthesis.
            ),
        ]),
        stage(
            "script",
            "script_approved" if review_only else "script",
            # Hash-pinned recovery performs only Voice's local verifier and
            # derives forum metadata from the raw bytes that the authority
            # binds. The authority may be a human frozen-text approval or a
            # generated CLI-review PASS receipt. It must not pull again or call
            # a script model. Normal generation still waits for the immutable
            # scene gate before model spend.
            deps=(
                ("runtime_assets",)
                if review_only
                else ("pull", "forum_meta", "scene")
            ),
            bindings=(
                {}
                if review_only
                else {
                    "input_01_pull": _reel_m4_binding(
                        "pull", "stage_bundle"),
                    "input_02_forum_meta": _reel_m4_binding(
                        "forum_meta", "stage_bundle"),
                }
            ),
            extra_input=(
                {"approved_script": approved_script} if review_only else None
            ),
        ),
        stage(
            "wardrobe_gate",
            "wardrobe_gate",
            deps=(
                ("script", "scene")
                if review_only
                else ("script", "forum_meta")
            ),
            bindings={
                "input_01_script": _reel_m4_binding("script", "stage_bundle"),
                "theme": _reel_m4_binding(
                    metadata_task, "forum_meta", select="json_value",
                    pointer="/category"),
            },
            reuse=False,
        ),
        _reel_m4_task(
            "cover",
            "castle.reel_cover",
            {
                "episode": episode,
                "output_root": str(cover_output_root),
                "cover_generator_version": "gpt-image-2-reel-cover-v1",
                "implementation_hash": _reel_m4_external_implementation_hash(
                    source_root, "cover"),
                "_orchestrator_reuse_authority": True,
            },
            # No gpt-image work starts until every spoken clip has a complete
            # blocking PASS bound by audio_ready authority.
            deps=(
                ("audio_listener_clips", "wardrobe_gate", "script")
                if review_only
                else (
                    "audio_listener_clips", "wardrobe_gate", "forum_meta",
                    "script",
                )
            ),
            bindings={
                "theme": _reel_m4_binding(
                    metadata_task, "forum_meta", select="json_value",
                    pointer="/category"),
                "topic": _reel_m4_binding(
                    metadata_task, "forum_meta", select="json_value",
                    pointer="/title"),
                "source_content_hash": _reel_m4_binding(
                    "script", "turnlist_reel", select="content_hash"),
                "wardrobe_bundle": _reel_m4_binding(
                    "wardrobe_gate", "stage_bundle"),
            },
            reuse=True,
        ),
        stage(
            "scene",
            "scene",
            # Runtime verification already audited the complete 20-theme
            # collection. This selected-theme gate only attests the pinned
            # existing PNG and never generates art during an episode run.
            deps=(
                ("runtime_assets", "script")
                if review_only
                else ("runtime_assets", "forum_meta")
            ),
            bindings={
                "theme": _reel_m4_binding(
                    metadata_task, "forum_meta", select="json_value",
                    pointer="/category"),
                "runtime_scene_assets_lock": _reel_m4_binding(
                    "runtime_assets", "runtime_scene_assets_lock"),
            },
            reuse=False,
        ),
        stage(
            "host_rig",
            "host_rig",
            deps=(
                ("scene", "script")
                if review_only
                else ("scene", "forum_meta")
            ),
            bindings={
                "theme": _reel_m4_binding(
                    metadata_task, "forum_meta", select="json_value",
                    pointer="/category"),
            },
            reuse=False,
        ),
        stage(
            "synth",
            "synth",
            deps=("script",),
            bindings={
                "input_01_script": _reel_m4_binding("script", "stage_bundle"),
                "runtime_host_voice_lock": _reel_m4_binding(
                    "runtime_assets", "runtime_host_voice_lock"),
                "runtime_asr_models_lock": _reel_m4_binding(
                    "runtime_assets", "runtime_asr_models_lock"),
            },
            gpu=True,
            extra_input=(
                {
                    "main_audio_resynth_idxs": main_audio_resynth_idxs,
                    "main_audio_cache_revision": main_audio_cache_revision,
                }
                if main_audio_resynth_idxs else None
            ),
        ),
        stage(
            "beats",
            "beats",
            deps=("script",) if review_only else ("script", "pull"),
            bindings=(
                {"input_01_script": _reel_m4_binding(
                    "script", "stage_bundle")}
                if review_only
                else {
                    "input_01_pull": _reel_m4_binding(
                        "pull", "stage_bundle"),
                    "input_02_script": _reel_m4_binding(
                        "script", "stage_bundle"),
                }
            ),
        ),
        stage(
            "peer_synth",
            "peer_synth",
            # GPU audio stages share one physical lane.  Chaining their hard
            # success dependencies costs no parallelism and prevents a doomed
            # run from starting auxiliary synthesis after main audio fails.
            deps=("beats", "synth"),
            bindings={
                "input_01_beats": _reel_m4_binding("beats", "stage_bundle"),
                "runtime_host_voice_lock": _reel_m4_binding(
                    "runtime_assets", "runtime_host_voice_lock"),
                "runtime_asr_models_lock": _reel_m4_binding(
                    "runtime_assets", "runtime_asr_models_lock"),
            },
            gpu=True,
        ),
        stage(
            "host_reaction_bank",
            "host_reaction_bank",
            deps=("script", "peer_synth"),
            bindings={
                "input_01_script": _reel_m4_binding("script", "stage_bundle"),
                "runtime_host_voice_lock": _reel_m4_binding(
                    "runtime_assets", "runtime_host_voice_lock"),
                "runtime_asr_models_lock": _reel_m4_binding(
                    "runtime_assets", "runtime_asr_models_lock"),
            },
            gpu=True,
        ),
        stage(
            "host_text",
            "host_text",
            deps=("script", "peer_synth", "host_reaction_bank"),
            bindings={
                "input_01_script": _reel_m4_binding("script", "stage_bundle"),
                "input_02_peer_synth": _reel_m4_binding("peer_synth", "stage_bundle"),
                "input_03_host_reaction_bank": _reel_m4_binding(
                    "host_reaction_bank", "stage_bundle"),
            },
        ),
        stage(
            "host_synth",
            "host_synth",
            deps=("host_text",),
            bindings={
                "input_01_host_text": _reel_m4_binding("host_text", "stage_bundle"),
                "runtime_host_voice_lock": _reel_m4_binding(
                    "runtime_assets", "runtime_host_voice_lock"),
                "runtime_asr_models_lock": _reel_m4_binding(
                    "runtime_assets", "runtime_asr_models_lock"),
            },
            gpu=True,
        ),
        stage(
            "champion_synth",
            "champion_synth",
            deps=(
                "host_text", "host_synth", "peer_synth",
                "host_reaction_bank",
            ),
            bindings={
                "input_01_script": _reel_m4_binding("script", "stage_bundle"),
                "input_02_peer_synth": _reel_m4_binding(
                    "peer_synth", "stage_bundle"),
                "input_03_host_reaction_bank": _reel_m4_binding(
                    "host_reaction_bank", "stage_bundle"),
                "input_04_host_text": _reel_m4_binding(
                    "host_text", "stage_bundle"),
                "input_05_host_synth": _reel_m4_binding(
                    "host_synth", "stage_bundle"),
                "runtime_host_voice_lock": _reel_m4_binding(
                    "runtime_assets", "runtime_host_voice_lock"),
                "runtime_asr_models_lock": _reel_m4_binding(
                    "runtime_assets", "runtime_asr_models_lock"),
                "runtime_sfx_sources_lock": _reel_m4_binding(
                    "runtime_assets", "runtime_sfx_sources_lock"),
            },
            gpu=True,
        ),
        stage(
            "audio_listener_clips",
            "audio_listener_clips",
            deps=(
                "synth", "peer_synth", "host_reaction_bank", "host_text",
                "host_synth", "champion_synth",
            ),
            bindings={
                "input_01_script": _reel_m4_binding("script", "stage_bundle"),
                "input_02_synth": _reel_m4_binding("synth", "stage_bundle"),
                "input_03_peer_synth": _reel_m4_binding("peer_synth", "stage_bundle"),
                "input_04_host_text": _reel_m4_binding("host_text", "stage_bundle"),
                "input_05_host_synth": _reel_m4_binding("host_synth", "stage_bundle"),
                "input_06_host_reaction_bank": _reel_m4_binding(
                    "host_reaction_bank", "stage_bundle"),
                "input_07_champion_synth": _reel_m4_binding(
                    "champion_synth", "stage_bundle"),
            },
            gpu=True,
        ),
        _reel_m4_task(
            "prepare_render",
            "castle.reel_prepare_render",
            {
                "episode": episode,
                "source_root": str(source_root),
                "output_root": str(render_input_root),
                "_orchestrator_reuse_authority": True,
            },
            deps=(
                "runtime_assets", "cover", "scene", "host_rig", "synth",
                "peer_synth", "host_reaction_bank", "host_text", "host_synth",
                "champion_synth", "audio_listener_clips",
            ),
            bindings={
                "runtime_receipt": _reel_m4_binding("runtime_assets", "runtime_receipt"),
                "runtime_host_voice_lock": _reel_m4_binding(
                    "runtime_assets", "runtime_host_voice_lock"),
                "runtime_asr_models_lock": _reel_m4_binding(
                    "runtime_assets", "runtime_asr_models_lock"),
                "runtime_scene_assets_lock": _reel_m4_binding(
                    "runtime_assets", "runtime_scene_assets_lock"),
                "runtime_sfx_sources_lock": _reel_m4_binding(
                    "runtime_assets", "runtime_sfx_sources_lock"),
                "runtime_sfx_outputs_lock": _reel_m4_binding(
                    "runtime_assets", "runtime_sfx_outputs_lock"),
                "runtime_music_lock": _reel_m4_binding(
                    "runtime_assets", "runtime_music_lock"),
                "theme": _reel_m4_binding(
                    metadata_task, "forum_meta", select="json_value",
                    pointer="/category"),
                "input_01_script": _reel_m4_binding("script", "stage_bundle"),
                "input_02_scene": _reel_m4_binding("scene", "stage_bundle"),
                "input_03_host_rig": _reel_m4_binding("host_rig", "stage_bundle"),
                "input_04_synth": _reel_m4_binding("synth", "stage_bundle"),
                "input_05_peer_synth": _reel_m4_binding("peer_synth", "stage_bundle"),
                "input_06_host_reactions": _reel_m4_binding(
                    "host_reaction_bank", "stage_bundle"),
                "input_07_host_text": _reel_m4_binding("host_text", "stage_bundle"),
                "input_08_host_synth": _reel_m4_binding("host_synth", "stage_bundle"),
                "input_09_champion_synth": _reel_m4_binding(
                    "champion_synth", "stage_bundle"),
                "input_10_audio_ready": _reel_m4_binding(
                    "audio_listener_clips", "stage_bundle"),
            },
            # This node is the only deliberate non-reuse node: it restores the
            # immutable upstream snapshots into the locked production
            # workspace before any render, including when every producer was
            # scheduler-reused.
            reuse=False,
        ),
        _reel_m4_task(
            "render_sisters",
            "ai_sister.render_sisters",
            {
                "episode": episode,
                "output_root": str(render_output_root),
                "reel_subdir": f"reel{episode}",
                "rough_mode": rough,
                "snapshot_segments": True,
                "implementation_hash": _reel_m4_external_implementation_hash(
                    source_root, "render_sisters"),
                "render_settings": {
                    "render_fps": 24,
                    "render_concurrency": 1 if rough else 2,
                    "video_preset": "ultrafast" if rough else "medium",
                    "video_crf": 30 if rough else 18,
                    "xfade": 0.4,
                    "music": False,
                    "skip_audio": True,
                },
                "_orchestrator_reuse_authority": True,
            },
            deps=("prepare_render", "cover"),
            bindings={
                "theme": _reel_m4_binding(
                    metadata_task, "forum_meta", select="json_value",
                    pointer="/category"),
                "turnlist_reel": _reel_m4_binding(
                    "prepare_render", "turnlist_reel", select="path_ref",
                    path=str(source_root / f"turnlist_reel5_{episode}.json")),
                "audio_manifest": _reel_m4_binding(
                    "prepare_render", "audio_manifest", select="path_ref",
                    path=str(render_input_root / "audio_manifest.json")),
                "peer_beats": _reel_m4_binding(
                    "prepare_render", "peer_beats", select="path_ref",
                    path=str(source_root / f"react_beats_peer_{episode}.json")),
                "rig_manifest": _reel_m4_binding(
                    "prepare_render", "rig_manifest", select="path_ref",
                    path=str(render_input_root / "rig_manifest.json")),
                "sfx_manifest": _reel_m4_binding(
                    "prepare_render", "sfx_manifest", select="path_ref",
                    path=str(render_input_root / "sfx_manifest.json")),
                "workspace_bundle": _reel_m4_binding(
                    "prepare_render", "workspace_bundle"),
                "runtime_sfx_sources_lock": _reel_m4_binding(
                    "runtime_assets", "runtime_sfx_sources_lock"),
                "runtime_sfx_outputs_lock": _reel_m4_binding(
                    "runtime_assets", "runtime_sfx_outputs_lock"),
                "source_content_hash": _reel_m4_binding(
                    "prepare_render", "workspace_bundle", select="content_hash"),
            },
            reuse=True,
            max_attempts=1 if review_only else 2,
        ),
        _reel_m4_task(
            "render_host",
            "ai_sister.render_host",
            {
                "episode": episode,
                "output_root": str(render_output_root),
                "reel_subdir": f"reel{episode}",
                "snapshot_segments": True,
                "adopt_proofs": False,
                "implementation_hash": _reel_m4_external_implementation_hash(
                    source_root, "render_host"),
                "render_settings": {
                    "render_fps": 24,
                    "render_concurrency": 1,
                    "video_preset": "ultrafast" if rough else "medium",
                    "video_crf": 30 if rough else 18,
                    "xfade": 0.4,
                    "champ_seed": episode,
                },
                "_orchestrator_reuse_authority": True,
            },
            deps=("prepare_render", "cover"),
            bindings={
                "theme": _reel_m4_binding(
                    metadata_task, "forum_meta", select="json_value",
                    pointer="/category"),
                "turnlist_reel": _reel_m4_binding(
                    "prepare_render", "turnlist_reel", select="path_ref",
                    path=str(source_root / f"turnlist_reel5_{episode}.json")),
                "turnlist_host": _reel_m4_binding(
                    "prepare_render", "turnlist_host", select="path_ref",
                    path=str(source_root / f"turnlist_host_{episode}.json")),
                "host_audio_manifest": _reel_m4_binding(
                    "prepare_render", "host_audio_manifest", select="path_ref",
                    path=str(render_input_root / "host_audio_manifest.json")),
                "peer_beats": _reel_m4_binding(
                    "prepare_render", "peer_beats", select="path_ref",
                    path=str(source_root / f"react_beats_peer_{episode}.json")),
                "host_beats": _reel_m4_binding(
                    "prepare_render", "host_beats", select="path_ref",
                    path=str(source_root / f"react_beats_host_{episode}.json")),
                "sister_rig_manifest": _reel_m4_binding(
                    "prepare_render", "rig_manifest", select="path_ref",
                    path=str(render_input_root / "rig_manifest.json")),
                "host_rig_manifest": _reel_m4_binding(
                    "prepare_render", "host_rig_manifest", select="path_ref",
                    path=str(render_input_root / "host_rig_manifest.json")),
                "champion_lines": _reel_m4_binding(
                    "prepare_render", "champion_lines", select="path_ref",
                    path=str(source_root / "champion_lines.json")),
                "champion_authority": _reel_m4_binding(
                    "prepare_render", "champion_authority", select="path_ref",
                    path=str(source_root / f"champion_{episode}.json")),
                "audio_ready": _reel_m4_binding(
                    "prepare_render", "audio_ready", select="path_ref",
                    path=str(source_root / f"audio_ready_{episode}.json")),
                "host_sfx_manifest": _reel_m4_binding(
                    "prepare_render", "host_sfx_manifest", select="path_ref",
                    path=str(render_input_root / "host_sfx_manifest.json")),
                "workspace_bundle": _reel_m4_binding(
                    "prepare_render", "workspace_bundle"),
                "runtime_sfx_sources_lock": _reel_m4_binding(
                    "runtime_assets", "runtime_sfx_sources_lock"),
                "runtime_sfx_outputs_lock": _reel_m4_binding(
                    "runtime_assets", "runtime_sfx_outputs_lock"),
                "source_content_hash": _reel_m4_binding(
                    "prepare_render", "workspace_bundle", select="content_hash"),
            },
            reuse=True,
            max_attempts=1 if review_only else 2,
        ),
        _reel_m4_task(
            "assemble",
            "castle.reel_assemble",
            {
                "episode": episode,
                "output_root": str(reel_root),
                "output_name": f"reel5_{episode}_final.mp4",
                "reel_subdir": f"reel{episode}",
                "cover_hold": 2.6,
                "music_vol": 0.16,
                "xfade": 0.4,
                "implementation_hash": _reel_m4_external_implementation_hash(
                    source_root, "assemble"),
                "_orchestrator_reuse_authority": True,
            },
            deps=("render_sisters", "render_host", "cover"),
            bindings={
                "theme": _reel_m4_binding(
                    metadata_task, "forum_meta", select="json_value",
                    pointer="/category"),
                "turnlist_reel": _reel_m4_binding(
                    "prepare_render", "turnlist_reel", select="path_ref",
                    path=str(source_root / f"turnlist_reel5_{episode}.json")),
                "turnlist_host": _reel_m4_binding(
                    "prepare_render", "turnlist_host", select="path_ref",
                    path=str(source_root / f"turnlist_host_{episode}.json")),
                "champion": _reel_m4_binding(
                    "champion_synth", "champion_authority"),
                "cover": _reel_m4_binding("cover", "cover"),
                "sisters_formula": _reel_m4_binding("render_sisters", "sisters_formula"),
                "host_formula": _reel_m4_binding("render_host", "host_formula"),
                "render_sisters_manifest": _reel_m4_binding(
                    "render_sisters", "render_sisters_manifest"),
                "render_host_manifest": _reel_m4_binding(
                    "render_host", "render_host_manifest"),
                "render_sisters_bundle": _reel_m4_binding(
                    "render_sisters", "sisters_segments"),
                "render_host_bundle": _reel_m4_binding(
                    "render_host", "host_segments"),
                "workspace_bundle": _reel_m4_binding(
                    "prepare_render", "workspace_bundle"),
                "music_bed": _reel_m4_binding("prepare_render", "music_bed"),
                "runtime_music_lock": _reel_m4_binding(
                    "runtime_assets", "runtime_music_lock"),
                "source_content_hash": _reel_m4_binding(
                    "prepare_render", "workspace_bundle", select="content_hash"),
            },
            reuse=True,
        ),
        _reel_m4_task(
            "review_proxy",
            "artifact.make_proxy",
            {
                "output_name": f"review_proxy_{episode}.mp4",
                "task_schema_version": PROXY_TASK_SCHEMA_VERSION,
                "implementation_hash": canonical_proxy_implementation_hash(
                    os.environ.get("FFMPEG_BIN")),
                **CANONICAL_REVIEW_PROXY_SETTINGS,
                "_orchestrator_reuse_authority": True,
            },
            deps=("assemble",),
            bindings={
                "source_artifact_id": _reel_m4_binding(
                    "assemble", "final", select="artifact_id"),
                "source_content_hash": _reel_m4_binding(
                    "assemble", "final", select="content_hash"),
            },
            reuse=True,
        ),
        stage(
            "audio_listener_final",
            "audio_listener_final",
            deps=("assemble", "prepare_render"),
            bindings={
                "input_01_workspace": _reel_m4_binding(
                    "prepare_render", "workspace_bundle"),
                "final_input": _reel_m4_binding("assemble", "final"),
            },
            gpu=True,
        ),
        _reel_m4_task(
            "qc",
            "castle.reel_qc",
            {
                "episode": episode,
                "reel_subdir": f"reel{episode}",
                "reel_aud": f"audio_reel5_{episode}",
                "host_aud": f"audio_host_{episode}",
                "output_root": str(qc_output_root),
                "skip_vision": False,
                "audio_only": False,
                "implementation_hash": _reel_m4_external_implementation_hash(
                    source_root, "qc"),
                "_orchestrator_reuse_authority": True,
            },
            deps=("audio_listener_final", "review_proxy", "cover"),
            bindings={
                "theme": _reel_m4_binding(
                    metadata_task, "forum_meta", select="json_value",
                    pointer="/category"),
                "final": _reel_m4_binding("assemble", "final"),
                "champion": _reel_m4_binding(
                    "champion_synth", "champion_authority"),
                "champion_bundle": _reel_m4_binding(
                    "champion_synth", "stage_bundle"),
                "render_sisters_bundle": _reel_m4_binding(
                    "render_sisters", "sisters_segments"),
                "render_host_bundle": _reel_m4_binding(
                    "render_host", "host_segments"),
                "reel_audio_manifest": _reel_m4_binding(
                    "prepare_render", "audio_manifest"),
                "host_audio_manifest": _reel_m4_binding(
                    "prepare_render", "host_audio_manifest"),
                "audio_listener_final_receipt": _reel_m4_binding(
                    "audio_listener_final", "audio_listener_final"),
                "cover": _reel_m4_binding("cover", "cover"),
                "cover_kv": _reel_m4_binding("cover", "cover_kv"),
                "cover_authority": _reel_m4_binding(
                    "cover", "cover_authority"),
                "cover_bundle": _reel_m4_binding("cover", "cover_bundle"),
                "workspace_bundle": _reel_m4_binding(
                    "prepare_render", "workspace_bundle"),
                "qc_rig_sources_manifest": _reel_m4_binding(
                    "prepare_render", "qc_rig_sources_manifest"),
                "qc_rig_bundle": _reel_m4_binding(
                    "prepare_render", "qc_rig_bundle"),
                "runtime_asr_models_lock": _reel_m4_binding(
                    "runtime_assets", "runtime_asr_models_lock"),
                "source_content_hash": _reel_m4_binding(
                    "assemble", "final", select="content_hash"),
            },
            reuse=True,
        ),
    ]
    return {
        "workflow_type": "ai_sister.reel_pipeline",
        "version": SUPPORTED_WORKFLOWS["ai_sister.reel_pipeline"]["version"],
        "tasks": tasks,
    }


def workflow_graph(workflow_type: str, payload: dict | None = None) -> dict:
    spec = SUPPORTED_WORKFLOWS.get(workflow_type)
    if not spec:
        raise ValueError(f"Unsupported workflow type: {workflow_type}")
    if spec.get("graph_builder") == "reel_m4":
        return reel_m4_graph(dict(payload or {}))
    return {
        "workflow_type": workflow_type,
        "version": spec["version"],
        "tasks": [
            {
                "key": "main",
                "task_type": spec["task_type"],
                "lane": spec["lane"],
                "max_attempts": spec.get("max_attempts", 1),
                "deps": [],
            }
        ],
    }


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _bounded_attempts(value, default: int = 1) -> int:
    try:
        attempts = int(value)
    except (TypeError, ValueError):
        attempts = default
    return max(1, min(attempts, 5))


def _custom_task_key(value: str, index: int) -> str:
    import re

    key = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    return (key or f"task_{index + 1}")[:80]


def normalize_custom_graph(body: dict, payload: dict) -> dict:
    task_specs = body.get("tasks")
    if not isinstance(task_specs, list):
        task_specs = payload.get("tasks")
    if not isinstance(task_specs, list) or not task_specs:
        raise ValueError("custom.dag requires a non-empty tasks array")
    if len(task_specs) > 64:
        raise ValueError("custom.dag accepts at most 64 tasks")

    global_input = payload.get("global_input")
    if not isinstance(global_input, dict):
        global_input = {
            key: value
            for key, value in payload.items()
            if key not in {"tasks", "global_input"}
        }
    default_attempts = _bounded_attempts(
        body.get("max_attempts")
        or body.get("maxAttempts")
        or payload.get("max_attempts")
        or payload.get("maxAttempts"),
        default=1,
    )
    default_reuse = _truthy(body.get("reuse") or payload.get("reuse"))

    normalized = []
    seen: set[str] = set()
    for index, raw in enumerate(task_specs):
        if not isinstance(raw, dict):
            raise ValueError(f"custom.dag task {index + 1} must be an object")
        task_type = str(raw.get("task_type") or raw.get("taskType") or raw.get("workflow_type") or "").strip()
        if task_type not in SUPPORTED_TASK_TYPES:
            raise ValueError(f"Unsupported custom.dag task_type: {task_type}")
        if task_type.startswith("castle.project_") and not project_qualification_enabled():
            raise ValueError("Castle project qualification is disabled")
        key = _custom_task_key(raw.get("key") or raw.get("id") or task_type, index)
        if key in seen:
            raise ValueError(f"Duplicate custom.dag task key: {key}")
        seen.add(key)
        task_input = dict(global_input)
        if isinstance(raw.get("input"), dict):
            task_input.update(raw["input"])
        deps = raw.get("deps") or raw.get("depends_on") or raw.get("dependsOn") or []
        if isinstance(deps, str):
            deps = [deps]
        if not isinstance(deps, list):
            raise ValueError(f"custom.dag task {key} deps must be an array")
        clean_deps = [_custom_task_key(dep, 0) for dep in deps if str(dep or "").strip()]
        bindings = normalize_task_bindings(raw.get("bindings"), task_key=key)
        for binding in bindings.values():
            producer = binding["task"]
            if producer not in clean_deps:
                clean_deps.append(producer)
        retry = raw.get("retry") if isinstance(raw.get("retry"), dict) else {}
        max_attempts = _bounded_attempts(
            raw.get("max_attempts")
            or raw.get("maxAttempts")
            or retry.get("max_attempts")
            or retry.get("maxAttempts"),
            default=default_attempts,
        )
        reuse = _truthy(raw.get("reuse")) or default_reuse
        normalized.append({
            "key": key,
            "task_type": task_type,
            "lane": SUPPORTED_TASK_TYPES[task_type]["lane"],
            "deps": clean_deps,
            "bindings": bindings,
            "input": task_input,
            "max_attempts": max_attempts,
            "reuse_policy": "input_hash" if reuse else "off",
        })

    known = {task["key"] for task in normalized}
    for task in normalized:
        for dep in task["deps"]:
            if dep not in known:
                raise ValueError(f"custom.dag task {task['key']} depends on unknown task: {dep}")
            if dep == task["key"]:
                raise ValueError(f"custom.dag task {task['key']} cannot depend on itself")
        for field, binding in task.get("bindings", {}).items():
            if binding["task"] not in known:
                raise ValueError(
                    f"custom.dag task {task['key']} binding {field} references unknown task: "
                    f"{binding['task']}")
            if binding["task"] == task["key"]:
                raise ValueError(
                    f"custom.dag task {task['key']} binding {field} cannot reference itself")
    _validate_acyclic(normalized)
    return {
        "workflow_type": CUSTOM_DAG_WORKFLOW_TYPE,
        "version": CUSTOM_DAG_VERSION,
        "tasks": normalized,
    }


def _validate_acyclic(tasks: list[dict]) -> None:
    deps = {task["key"]: set(task.get("deps") or []) for task in tasks}
    temporary: set[str] = set()
    permanent: set[str] = set()

    def visit(key: str) -> None:
        if key in permanent:
            return
        if key in temporary:
            raise ValueError(f"custom.dag contains a dependency cycle at {key}")
        temporary.add(key)
        for dep in deps.get(key, set()):
            visit(dep)
        temporary.remove(key)
        permanent.add(key)

    for task in tasks:
        visit(task["key"])


def infer_title(workflow_type: str, payload: dict) -> str:
    explicit = str(payload.get("title") or "").strip()
    if explicit:
        return explicit[:160]
    if workflow_type == "mock.echo":
        return "Mock echo"
    if workflow_type == "ai_sister.reel_assemble_only":
        episode = str(payload.get("episode") or payload.get("reel_id") or "").strip()
        return f"AI-Sister assemble {episode}" if episode else "AI-Sister assemble only"
    if workflow_type == "ai_sister.reel_pipeline":
        episode = str(payload.get("episode") or payload.get("reel_id") or "").strip()
        return f"AI-Sister reel {episode}" if episode else "AI-Sister reel pipeline"
    if workflow_type == "castle.voice_speak_guarded":
        return "打字即說（高品質）"
    if workflow_type == "castle.reel_cover":
        episode = str(payload.get("episode") or "").strip()
        topic = str(payload.get("topic") or "").strip()
        return f"封面 {episode} {topic}".strip()
    if workflow_type == "castle.reel_assemble":
        episode = str(payload.get("episode") or "").strip()
        return f"合成 {episode}".strip()
    if workflow_type == "castle.reel_qc":
        episode = str(payload.get("episode") or "").strip()
        return f"品管 {episode}".strip()
    if workflow_type == "castle.voxcpm_tts":
        return "VoxCPM 多語音色"
    if workflow_type == "castle.voxcpm_tts_batch":
        segments = payload.get("segments") if isinstance(payload.get("segments"), list) else []
        return f"簡報配音 {len(segments)} 段"
    if workflow_type == "artifact.make_proxy":
        return "Make artifact proxy"
    if workflow_type == CUSTOM_DAG_WORKFLOW_TYPE:
        return str(payload.get("title") or "Custom DAG workflow")[:160]
    return workflow_type


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _cancelled(cancel_file: Path) -> bool:
    return cancel_file.exists()


def _path(value: str, field: str, must_exist: bool = False) -> Path:
    if not str(value or "").strip():
        raise ValueError(f"{field} is required")
    path = Path(str(value)).expanduser().resolve()
    if must_exist and not path.exists():
        raise ValueError(f"{field} not found: {path}")
    return path


def _env_path(name: str, default: Path | str) -> Path:
    return Path(os.environ.get(name, str(default))).expanduser().resolve()


def _trusted_existing_path(name: str, default: Path | str, field: str, *, file: bool = True) -> Path:
    # No resolve(): venv interpreters (.venv/bin/python) are symlinks and must
    # be executed via the symlink path or they lose the venv's site-packages.
    path = Path(os.environ.get(name, str(default))).expanduser().absolute()
    if file and not path.is_file():
        raise ValueError(f"{field} not found: {path}")
    if not file and not path.exists():
        raise ValueError(f"{field} not found: {path}")
    return path


def _task_allowed_roots() -> list[Path]:
    settings = settings_from_env()
    roots = [
        Path(os.environ.get("COMFYUI_OUTPUT_DIR", Path.home() / "comfyui/output")),
        Path(os.environ.get("COMFYUI_INPUT_DIR", Path.home() / "comfyui/input")),
        Path(os.environ.get("COMFYUI_TEMP_DIR", Path.home() / "comfyui/temp")),
        Path(os.environ.get("CASTLE_CRAWL_VIDEO_ROOT", Path.home() / ".local/share/web-comfyui/integrations/crawler/downloads")),
        Path.home() / ".local/share/web-comfyui/integrations/voice/video-mvp",
        Path.home() / ".local/share/web-comfyui/integrations/voice/train_data/custom_en_voxcpm",
        settings.artifact_root,
        settings.work_root,
    ]
    for env_name in ("CASTLE_ORCH_TASK_ALLOWED_ROOTS", "CASTLE_ORCH_ADOPT_ALLOWED_ROOTS"):
        for part in os.environ.get(env_name, "").split(os.pathsep):
            if part.strip():
                roots.append(Path(part).expanduser())
    resolved = []
    for root in roots:
        try:
            resolved.append(root.expanduser().resolve())
        except OSError:
            continue
    return resolved


def _path_is_allowed(path: Path, roots: list[Path]) -> bool:
    for root in roots:
        try:
            if path.is_relative_to(root):
                return True
        except AttributeError:
            if os.path.commonpath([str(root), str(path)]) == str(root):
                return True
    return False


def _safe_existing_input_path(value: str, field: str) -> Path:
    path = _path(value, field, must_exist=True)
    if not _path_is_allowed(path, _task_allowed_roots()):
        raise ValueError(f"{field} is outside allowed orchestrator roots")
    if not path.is_file():
        raise ValueError(f"{field} is not a file: {path}")
    return path


def _safe_existing_input_dir_or_file(value: str, field: str) -> Path:
    # VoxCPM LoRA checkpoints are directories (lora_config.json + weights).
    path = _path(value, field, must_exist=True)
    if not _path_is_allowed(path, _task_allowed_roots()):
        raise ValueError(f"{field} is outside allowed orchestrator roots")
    if not path.is_file() and not path.is_dir():
        raise ValueError(f"{field} is not a file or directory: {path}")
    return path


def _safe_output_path(value: str, field: str) -> Path:
    path = _path(value, field, must_exist=False)
    if not _path_is_allowed(path, _task_allowed_roots()):
        raise ValueError(f"{field} is outside allowed orchestrator roots")
    return path


def _safe_output_dir(value: str, field: str) -> Path:
    path = _path(value, field, must_exist=False)
    if not _path_is_allowed(path, _task_allowed_roots()):
        raise ValueError(f"{field} is outside allowed orchestrator roots")
    path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise ValueError(f"{field} is not a directory")
    return path


def _safe_output_name(name: str, fallback: str = "output.bin") -> str:
    name = Path(str(name or "")).name.strip() or fallback
    name = SAFE_NAME_RE.sub("_", name).strip("._")
    return name or fallback


def _atomic_copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.tmp-{os.getpid()}")
    shutil.copy2(src, tmp)
    os.replace(tmp, dest)


def _atomic_copy_hash_pinned(src: Path, dest: Path, expected_hash: str) -> None:
    """Copy one immutable authority without a hash/check-to-copy race."""
    if not re.fullmatch(r"[0-9a-f]{64}", str(expected_hash or "")):
        raise ValueError("expected copy hash must be a lowercase SHA-256")
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_name(f".{dest.name}.tmp-{os.getpid()}-{time.time_ns()}")
    source_fd = None
    try:
        source_fd = os.open(
            src,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        before = os.fstat(source_fd)
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeError("hash-pinned copy source is not a regular file")
        digest = hashlib.sha256()
        with os.fdopen(source_fd, "rb", closefd=True) as source:
            source_fd = None
            with temporary.open("xb") as target:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            after = os.fstat(source.fileno())
        stable_fields = (
            "st_dev", "st_ino", "st_mode", "st_size",
            "st_mtime_ns", "st_ctime_ns",
        )
        if any(
            getattr(before, field) != getattr(after, field)
            for field in stable_fields
        ):
            raise RuntimeError("hash-pinned copy source changed while reading")
        if digest.hexdigest() != expected_hash:
            raise RuntimeError("hash-pinned copy source content_hash mismatch")
        temporary.chmod(0o600)
        os.replace(temporary, dest)
    finally:
        if source_fd is not None:
            os.close(source_fd)
        temporary.unlink(missing_ok=True)


def gpu_queue_cmd(inner_argv: list[str], payload: dict) -> list[str]:
    settings = settings_from_env()
    script = settings.gpu_queue_script.expanduser().resolve()
    if not script.is_file():
        raise ValueError(f"gpu_queue.py not found: {script}")
    python = str(settings.gpu_queue_python)
    min_free = str(settings.gpu_min_free_mb)
    max_util = str(settings.gpu_max_util)
    interval = str(settings.gpu_queue_interval)
    return [
        python,
        str(script),
        "--min-free-mb", min_free,
        "--max-util", max_util,
        "--interval", interval,
        "--",
        *inner_argv,
    ]


def run_mock_echo(payload: dict, work_dir: Path, cancel_file: Path) -> list[dict]:
    try:
        attempt = int(payload.get("_attempt") or 0)
    except (TypeError, ValueError):
        attempt = 0
    try:
        fail_until = int(payload.get("fail_until_attempt") if payload.get("fail_until_attempt") is not None else -1)
    except (TypeError, ValueError):
        fail_until = -1
    if attempt < fail_until:
        raise RuntimeError(f"mock requested failure for attempt {attempt}")
    delay = max(0.0, min(float(payload.get("sleep_seconds") or 0), 600.0))
    end = time.time() + delay
    while time.time() < end:
        if _cancelled(cancel_file):
            raise KeyboardInterrupt()
        time.sleep(min(0.2, end - time.time()))
    text = str(payload.get("message") or "mock ok")
    out = work_dir / "tmp" / "echo.txt"
    _write_text(out, text + "\n")
    return [
        {
            "path": str(out),
            "name": str(payload.get("output_name") or "echo.txt"),
            "artifact_type": "text",
            "output_name": "echo",
            "metadata": {"message_length": len(text)},
        }
    ]


def run_mock_gpu_echo(payload: dict, work_dir: Path, cancel_file: Path) -> list[dict]:
    out = work_dir / "tmp" / "gpu_echo.txt"
    script = (
        "from pathlib import Path; import sys, time; "
        "Path(sys.argv[1]).write_text(sys.argv[2] + '\\n', encoding='utf-8')"
    )
    inner = [sys.executable, "-c", script, str(out), str(payload.get("message") or "gpu mock ok")]
    subprocess.check_call(gpu_queue_cmd(inner, payload), cwd=str(work_dir))
    if _cancelled(cancel_file):
        raise KeyboardInterrupt()
    return [
        {
            "path": str(out),
            "name": str(payload.get("output_name") or "gpu_echo.txt"),
            "artifact_type": "text",
            "output_name": "gpu_echo",
            "metadata": {"gpu_queue_wrapped": True},
        }
    ]


def _read_concat_segments(path: Path) -> list[str]:
    segments = []
    base = path.parent
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*file\s+'(.+)'\s*$", line)
        if m:
            segment = Path(m.group(1)).expanduser()
            if not segment.is_absolute():
                segment = base / segment
            segments.append(str(segment))
    return segments


def run_ai_sister_reel_assemble(payload: dict, work_dir: Path, cancel_file: Path) -> list[dict]:
    segments = [str(p) for p in (payload.get("segments") or []) if str(p).strip()]
    concat_list = str(payload.get("concat_list_path") or "").strip()
    if concat_list and not segments:
        segments = _read_concat_segments(_safe_existing_input_path(concat_list, "concat_list_path"))
    source = str(payload.get("source_path") or "").strip()
    if source and not segments:
        segments = [source]
    if not segments:
        raise ValueError("ai_sister.reel_assemble_only requires source_path, segments, or concat_list_path")

    resolved = [_safe_existing_input_path(p, "segment") for p in segments]
    if _cancelled(cancel_file):
        raise KeyboardInterrupt()

    output_name = _safe_output_name(str(payload.get("output_name") or "reel_assembled.mp4"), "reel_assembled.mp4")
    if not Path(output_name).suffix and resolved:
        output_name += resolved[0].suffix or ".bin"
    out = (work_dir / "tmp" / output_name).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    if len(resolved) == 1:
        shutil.copy2(resolved[0], out)
    else:
        script = Path(os.environ.get(
            "CASTLE_AI_SISTER_ASSEMBLE_XFADE",
            Path.home() / ".local/share/web-comfyui/integrations/voice/video-mvp/assemble_xfade.py",
        ))
        if not script.is_file():
            raise ValueError(f"assemble_xfade.py not found: {script}")
        list_path = work_dir / "tmp" / "concat.txt"
        _write_text(list_path, "".join(f"file '{p}'\n" for p in resolved))
        transition_seconds = str(payload.get("transition_seconds") or "0.4")
        transition = str(payload.get("transition") or "fade")
        cmd = [sys.executable, str(script), str(out), str(list_path), transition_seconds, transition]
        subprocess.check_call(cmd, cwd=str(script.parent))
    return [
        {
            "path": str(out),
            "name": output_name,
            "artifact_type": "video" if out.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv"} else "file",
            "output_name": "assembled",
            "metadata": {
                "segments": len(resolved),
                "workflow": "ai_sister.reel_assemble_only",
            },
        }
    ]


def _episode_id(payload: dict) -> str:
    raw = payload.get("episode")
    if raw is None:
        raw = payload.get("reel_id")
    if isinstance(raw, (list, tuple)):
        raise ValueError("ai_sister.reel_pipeline is single-episode only")
    text = str(raw or "").strip()
    if not re.fullmatch(r"[0-9]{1,8}", text):
        raise ValueError("ai_sister.reel_pipeline requires one numeric episode")
    for multi_key in ("episodes", "reel_ids", "batch", "ids"):
        if multi_key in payload and payload.get(multi_key):
            raise ValueError("ai_sister.reel_pipeline is single-episode only")
    return text


def _reel_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


MAIN_AUDIO_RESYNTH_MAX_IDXS = 8
MAIN_AUDIO_CACHE_REVISION_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}")


def _reel_main_audio_resynth_idxs(payload: dict) -> list[int]:
    raw = payload.get("main_audio_resynth_idxs")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("main_audio_resynth_idxs must be an array of turn indexes")
    clean: list[int] = []
    seen: set[int] = set()
    for value in raw:
        if type(value) is not int or value < 0 or value > 99:
            raise ValueError("main_audio_resynth_idxs must contain indexes 0..99")
        if value in seen:
            continue
        seen.add(value)
        clean.append(value)
    if len(clean) > MAIN_AUDIO_RESYNTH_MAX_IDXS:
        raise ValueError("main_audio_resynth_idxs is too large for one repair")
    return sorted(clean)


def _reel_main_audio_cache_revision(payload: dict, *, required: bool) -> str:
    raw = payload.get("main_audio_cache_revision")
    if raw is None and not required:
        return ""
    revision = str(raw or "").strip()
    if MAIN_AUDIO_CACHE_REVISION_RE.fullmatch(revision) is None:
        raise ValueError(
            "main_audio_cache_revision is required for partial main audio resynth"
        )
    return revision


def _reel_stage_value(value, field: str) -> str:
    stage = str(value or "").strip()
    if stage and stage not in REEL5_STAGES:
        raise ValueError(f"{field} must be one of: {', '.join(REEL5_STAGES)}")
    return stage


def _reel_force_stages(payload: dict) -> list[str]:
    raw = payload.get("force_stages") if "force_stages" in payload else payload.get("forceStages")
    if raw in (None, ""):
        return []
    if isinstance(raw, str):
        items = raw.split(",")
    elif isinstance(raw, list):
        items = raw
    else:
        raise ValueError("force_stages must be a list or comma-separated string")
    stages = []
    for item in items:
        stage = _reel_stage_value(item, "force_stages")
        if stage and stage not in stages:
            stages.append(stage)
    return stages


def _reel_from_stage(payload: dict) -> str:
    return _reel_stage_value(payload.get("from_stage") or payload.get("fromStage") or "", "from_stage")


def _clean_child_env() -> dict:
    blocked = {"CASTLE_REEL_NATIVE_PYTHON"}
    return {
        key: value for key, value in os.environ.items()
        if key not in blocked and not SECRET_ENV_RE.search(key)
    }


def _clamped_float(value, default: float, low: float, high: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(low, min(high, parsed))


def _terminate_process_group(proc: subprocess.Popen, grace_seconds: float = 3.0) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        proc.terminate()
    deadline = time.monotonic() + grace_seconds
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.1)
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        except OSError:
            proc.kill()


def _reader_thread(stream, lines: queue.Queue) -> None:
    try:
        for line in stream:
            lines.put(line)
    finally:
        try:
            stream.close()
        except Exception:
            pass
        lines.put(None)


def run_castle_voice_speak_guarded(payload: dict, work_dir: Path, cancel_file: Path) -> list[dict]:
    chunks = [str(chunk).strip() for chunk in (payload.get("chunks") or []) if str(chunk).strip()]
    if not chunks:
        raise ValueError("chunks are required")
    voice_python = _trusted_existing_path("CASTLE_VOICE_LAB_PYTHON", Path.home() / ".local/share/web-comfyui/integrations/voice/.venv/bin/python", "voice_python")
    worker = _trusted_existing_path("CASTLE_GUARDED_TTS_SCRIPT", WEBUI_ROOT / "guarded_breezy_tts.py", "worker_script")
    output = _safe_output_path(payload.get("output_path"), "output_path")
    sidecar = _safe_output_path(payload.get("sidecar_path"), "sidecar_path")
    tmp_output = work_dir / "tmp" / output.name
    tmp_sidecar = work_dir / "tmp" / sidecar.name
    chunks_path = work_dir / "tmp" / "chunks.json"
    _write_text(chunks_path, json.dumps({"chunks": chunks}, ensure_ascii=False, indent=2))
    job_id = str(payload.get("_run_id") or payload.get("_task_id") or "orchestrator")
    inner = [
        str(voice_python),
        str(worker),
        "--persona", str(payload.get("persona") or ""),
        "--chunks", str(chunks_path),
        "--output", str(tmp_output),
        "--sidecar", str(tmp_sidecar),
        "--job-id", job_id,
        "--workflow", str(payload.get("workflow") or "castle-voice-speak-guarded-web"),
        "--breezy-url", os.environ.get("CASTLE_BREEZY_URL", "http://127.0.0.1:8231").rstrip("/"),
        "--whisper-model", os.environ.get("CASTLE_GUARD_WHISPER_MODEL", "large-v3"),
        "--language", str(payload.get("language") or "zh"),
        "--tries", str(payload.get("tries") or 4),
        "--cer", str(payload.get("cer") or 0.22),
        "--pause", str(payload.get("pause") or 0.14),
        "--cancel-file", str(cancel_file),
    ]
    subprocess.check_call(gpu_queue_cmd(inner, payload), cwd=str(worker.parent))
    if _cancelled(cancel_file):
        raise KeyboardInterrupt()
    _atomic_copy(tmp_output, output)
    _atomic_copy(tmp_sidecar, sidecar)
    return [
        {
            "path": str(output),
            "name": output.name,
            "artifact_type": "audio",
            "output_name": "audio",
            "metadata": {
                "workflow": "castle.voice_speak_guarded",
                "persona": str(payload.get("persona") or ""),
                "chunks": len(chunks),
            },
        }
    ]


def _record_task_event(payload: dict, event_type: str, message: str, data: dict | None = None, *, level: str = "info") -> None:
    run_id = str(payload.get("_run_id") or "").strip()
    task_id = str(payload.get("_task_id") or "").strip()
    if not run_id or not task_id:
        return
    try:
        with connect() as conn:
            with transaction(conn):
                add_event(conn, event_type, message, run_id=run_id, task_id=task_id, data=data or {}, level=level)
    except Exception:
        pass


def _runner_internal_reuse_allowed(payload: dict) -> bool:
    """M3 standalone tasks may self-reuse; M4 delegates reuse to the DAG."""
    return not _reel_bool(payload.get("_orchestrator_reuse_authority"), False)


def _number(value) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _redact_error_text(value) -> str:
    """Strip secrets, local paths and URLs. Never clips - redaction grows the
    text, so the caller has to clip afterwards or it clips the wrong amount."""
    text = str(value if value is not None else "")
    text = SECRET_ENV_RE.sub("[redacted]", text)
    text = LOCAL_PATH_RE.sub("[local-path]", text)
    text = URL_RE.sub("[url]", text)
    text = re.sub(r"sk-[A-Za-z0-9_-]+", "[redacted]", text)
    return re.sub(r"\s+", " ", text).strip()


def _sanitize_error_text(value, limit: int = 360) -> str:
    return _redact_error_text(value)[:limit] or "see task log"


def _sanitize_error_tail(value, limit: int = 900) -> str:
    """Redact like _sanitize_error_text, but keep the end of the output.

    A failing stage prints its banner first and its traceback last. Clipping
    the head kept the banner and threw away the cause: every GPU stage failure
    was stored as "[gpu-queue] waiting for lock ... running: ... Traceback
    (most recent call last): File" and nothing more, which is why so many
    unrelated failures looked like queue contention. Callers that normalise a
    field value rather than explain a failure must keep using the head.
    """
    text = _redact_error_text(value)
    if not text:
        return "see task log"
    if len(text) <= limit:
        return text
    return "..." + text[-(limit - 3):]


def _require_reel_cover_payload(payload: dict) -> dict:
    required = {
        "episode",
        "theme",
        "topic",
        "output_root",
        "source_content_hash",
        "cover_generator_version",
    }
    optional = {"implementation_hash", "wardrobe_bundle", "wardrobe_bundle_hash"}
    allowed = required | optional
    for field in sorted(REEL_COVER_MODEL_OVERRIDE_FIELDS):
        if field in payload:
            raise ValueError(f"castle.reel_cover rejects model override field: {field}")
    unknown = sorted(key for key in payload if not str(key).startswith("_") and key not in allowed)
    if unknown:
        raise ValueError(f"castle.reel_cover unknown fields: {', '.join(unknown)}")
    missing = sorted(key for key in required if key not in payload)
    if missing:
        raise ValueError(f"castle.reel_cover missing fields: {', '.join(missing)}")

    episode = str(payload.get("episode") or "").strip()
    if not re.fullmatch(r"[0-9]+", episode):
        raise ValueError("episode must contain digits only")
    theme = str(payload.get("theme") or "").strip()
    if not theme:
        raise ValueError("theme is required")
    topic = str(payload.get("topic") or "").strip()
    if not topic:
        raise ValueError("topic is required")
    source_content_hash = str(payload.get("source_content_hash") or "").strip()
    if not source_content_hash:
        raise ValueError("source_content_hash is required")
    cover_generator_version = str(payload.get("cover_generator_version") or "").strip()
    if not cover_generator_version:
        raise ValueError("cover_generator_version is required")
    output_root = _safe_output_path(payload.get("output_root"), "output_root")
    wardrobe_bundle_ref = None
    if "wardrobe_bundle" in payload:
        wardrobe_bundle_ref = _resolve_reel_input_ref(
            _reel_ref_payload(payload.get("wardrobe_bundle"), "wardrobe_bundle"),
            "wardrobe_bundle",
        )
        if wardrobe_bundle_ref["kind"] != "artifact":
            raise ValueError("wardrobe_bundle must be an adopted Castle artifact")
        _require_reel_artifact_producer(
            wardrobe_bundle_ref,
            "wardrobe_bundle",
            workflow="castle.reel_native_stage",
            role="stage_bundle",
        )
        if wardrobe_bundle_ref.get("metadata", {}).get("stage") != "wardrobe_gate":
            raise ValueError("wardrobe_bundle must come from the wardrobe_gate stage")
    declared_wardrobe_hash = str(payload.get("wardrobe_bundle_hash") or "").strip()
    if (
        wardrobe_bundle_ref is not None
        and declared_wardrobe_hash
        and declared_wardrobe_hash != wardrobe_bundle_ref["content_hash"]
    ):
        raise ValueError("wardrobe_bundle_hash differs from wardrobe_bundle")
    wardrobe_bundle_hash = (
        wardrobe_bundle_ref["content_hash"]
        if wardrobe_bundle_ref is not None else declared_wardrobe_hash
    )
    return {
        "episode": episode,
        "theme": theme,
        "topic": topic,
        "output_root": output_root,
        "source_content_hash": source_content_hash,
        "cover_generator_version": cover_generator_version,
        "implementation_hash": str(payload.get("implementation_hash") or "").strip(),
        "wardrobe_bundle": payload.get("wardrobe_bundle"),
        "wardrobe_bundle_ref": wardrobe_bundle_ref,
        "wardrobe_bundle_hash": wardrobe_bundle_hash,
    }


def _reel_cover_input_hash(spec: dict, implementation_hash: str) -> str:
    return input_hash({
        "episode": spec["episode"],
        "theme": spec["theme"],
        "topic": spec["topic"],
        "source_content_hash": spec["source_content_hash"],
        "cover_generator_version": spec["cover_generator_version"],
        "implementation_hash": implementation_hash,
        "wardrobe_bundle_hash": spec["wardrobe_bundle_hash"],
        "task_schema_version": REEL_COVER_TASK_SCHEMA_VERSION,
    })


def reel_cover_postgen_retry_eligible(error: object) -> bool:
    """Return whether a failed cover reached the post-provider handoff boundary."""
    detail = str(error or "")
    return any(detail.startswith(prefix) for prefix in REEL_COVER_POSTGEN_FAILURE_PREFIXES)


def _reel_cover_tags(spec: dict) -> list[str]:
    return [
        "project:ai-sister",
        "workflow:reel5",
        f"episode:{spec['episode']}",
        "stage:cover",
        "role:cover",
        f"theme:{_safe_output_name(spec['theme'], 'theme')}",
    ]


def _reel_cover_role_tags(spec: dict, role: str) -> list[str]:
    return [
        *(tag for tag in _reel_cover_tags(spec) if not tag.startswith("role:")),
        f"role:{role}",
    ]


def _reel_cover_reusable_outputs(spec: dict, cover_input_hash: str) -> list[dict] | None:
    with connect() as conn:
        rows = conn.execute(
            """
            select * from artifacts
            where json_extract(metadata_json, '$.workflow') = 'castle.reel_cover'
              and json_extract(metadata_json, '$.episode') = ?
              and json_extract(metadata_json, '$.stage') = 'cover'
              and json_extract(metadata_json, '$.input_hash') = ?
              and status = 'available'
              and artifact_type in ('image', 'json', 'archive')
            order by created_at desc
            """,
            (spec["episode"], cover_input_hash),
        ).fetchall()
    groups: dict[str, dict[str, dict]] = {}
    group_order: list[str] = []
    for row in rows:
        item = dict(row)
        group_key = str(item.get("producing_task_id") or item.get("run_id") or item.get("id"))
        if group_key not in groups:
            groups[group_key] = {}
            group_order.append(group_key)
        metadata = json.loads(item.get("metadata_json") or "{}")
        role = str(metadata.get("role") or "")
        artifact_type = str(item.get("artifact_type") or "")
        output_name = ""
        if role == "cover" and artifact_type == "image":
            output_name = "cover"
        elif role == "cover" and artifact_type == "json":
            output_name = "metadata"
        elif role in {"cover_kv", "cover_authority", "cover_bundle"}:
            output_name = role
        if output_name:
            groups[group_key][output_name] = item
    required = {
        "cover": "image",
        "metadata": "json",
        "cover_kv": "json",
        "cover_authority": "json",
        "cover_bundle": "archive",
    }
    by_name = None
    for group_key in group_order:
        candidate = groups[group_key]
        if all(
            name in candidate and candidate[name].get("artifact_type") == artifact_type
            for name, artifact_type in required.items()
        ):
            by_name = candidate
            break
    if by_name is None:
        return None
    settings = settings_from_env()
    outputs = []
    common_audit = None
    expected_roles = {
        "cover": "cover",
        "metadata": "cover",
        "cover_kv": "cover_kv",
        "cover_authority": "cover_authority",
        "cover_bundle": "cover_bundle",
    }
    tag_roles = {
        **expected_roles,
        "metadata": "cover_metadata",
    }
    for output_name, artifact_type in required.items():
        row = by_name[output_name]
        rel = str(row.get("relative_path") or "")
        if not rel:
            return None
        path = (settings.artifact_root / rel).resolve()
        if (
            not path.is_file()
            or not re.fullmatch(r"[0-9a-f]{64}", str(row.get("content_hash") or ""))
            or _file_sha256(path) != row["content_hash"]
        ):
            return None
        metadata = json.loads(row.get("metadata_json") or "{}")
        try:
            audit = validate_cover_audit(metadata.get("cover_audit"))
        except (TypeError, ValueError):
            return None
        if (
            metadata.get("task_schema_version") != REEL_COVER_TASK_SCHEMA_VERSION
            or metadata.get("role") != expected_roles[output_name]
        ):
            return None
        if common_audit is None:
            common_audit = audit
        elif common_audit != audit:
            return None
        metadata["idempotent_reuse"] = True
        metadata["reused_from_artifact_id"] = row.get("id")
        metadata["reused_from_task_id"] = row.get("producing_task_id")
        outputs.append({
            "path": str(path),
            "name": path.name,
            "artifact_type": artifact_type,
            "output_name": output_name,
            "metadata": metadata,
            "tags": _reel_cover_role_tags(spec, tag_roles[output_name]),
        })
    try:
        validate_cover_audit(
            common_audit,
            expected_final_sha256=str(by_name["cover"].get("content_hash") or ""),
            expected_metadata_sha256=str(
                by_name["cover_kv"].get("content_hash") or ""),
            expected_authority_sha256=str(
                by_name["cover_authority"].get("content_hash") or ""),
        )
    except (TypeError, ValueError):
        return None
    return outputs


def _sanitize_reel_cover_sidecar(sidecar: dict) -> dict:
    clean: dict = {}
    for key in ("prompt_sha256", "topic", "theme", "request_id", "response_id"):
        value = sidecar.get(key)
        if isinstance(value, str) and value.strip():
            clean[key] = _sanitize_error_text(value, 240)
    for key in (
        "image_count",
        "billed_input_tokens",
        "billed_output_tokens",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cost_usd",
    ):
        value = _number(sidecar.get(key))
        if value is not None:
            clean[key] = value
    usage = sidecar.get("usage") if isinstance(sidecar.get("usage"), dict) else {}
    clean_usage = {}
    for key in ("input_tokens", "output_tokens", "total_tokens", "image_count"):
        value = _number(usage.get(key))
        if value is not None:
            clean_usage[key] = value
    if clean_usage:
        clean["usage"] = clean_usage
    return clean


def _reel_cover_metadata(
    spec: dict,
    cover_input_hash: str,
    implementation_hash: str,
    image_path: Path,
    sidecar: dict,
    cover_audit: dict,
    key_visual_receipt: dict | None = None,
) -> dict:
    clean_sidecar = _sanitize_reel_cover_sidecar(sidecar)
    clean_audit = validate_cover_audit(
        cover_audit, expected_final_sha256=_file_sha256(image_path))
    cost = {
        "provider": "openai",
        "model": "gpt-image-2",
        "image_count": int(clean_sidecar.get("image_count") or 1),
        "output_bytes": image_path.stat().st_size,
        "cost_estimate_version": REEL_COVER_COST_ESTIMATE_VERSION,
    }
    for key in ("prompt_sha256", "topic", "theme", "request_id", "response_id", "usage"):
        if key in clean_sidecar:
            cost[key] = clean_sidecar[key]
    for key in ("billed_input_tokens", "billed_output_tokens", "input_tokens", "output_tokens", "total_tokens", "cost_usd"):
        if key in clean_sidecar:
            cost[key] = clean_sidecar[key]
    metadata = {
        "workflow": "castle.reel_cover",
        "episode": spec["episode"],
        "stage": "cover",
        "role": "cover",
        "theme": _sanitize_error_text(spec["theme"], 120),
        "topic": _sanitize_error_text(spec["topic"], 240),
        "input_hash": cover_input_hash,
        "source_content_hash": _sanitize_error_text(spec["source_content_hash"], 160),
        "implementation_hash": implementation_hash,
        "wardrobe_bundle_hash": spec["wardrobe_bundle_hash"],
        "cover_generator_version": _sanitize_error_text(spec["cover_generator_version"], 120),
        "task_schema_version": REEL_COVER_TASK_SCHEMA_VERSION,
        "cover_audit": clean_audit,
        "cost": cost,
    }
    if key_visual_receipt is not None:
        receipt_path = _reel_cover_key_visual_receipt_path(
            _configured_reel_source_root(), spec["episode"])
        metadata["key_visual_cache"] = {
            "schema": key_visual_receipt["schema"],
            "input_hash": key_visual_receipt["key_visual_input_hash"],
            "kv_png_sha256": key_visual_receipt["kv_png_sha256"],
            "receipt_sha256": _file_sha256(receipt_path),
        }
    return metadata


def _reel_cover_billing_marker(output: str) -> str | None:
    lowered = output.lower()
    for marker in REEL_COVER_BILLING_MARKERS:
        if marker.startswith("HTTP "):
            if marker.lower() in lowered or marker.split(" ", 1)[1] in lowered and "http" in lowered:
                return marker
        elif marker.lower() in lowered:
            return marker
    return None


def _run_cpu_child(
    cmd: list[str],
    cwd: Path,
    cancel_file: Path,
    *,
    env: dict | None = None,
    error_label: str = "child process",
) -> str:
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=True,
    )
    assert proc.stdout is not None
    lines: queue.Queue = queue.Queue()
    reader = threading.Thread(target=_reader_thread, args=(proc.stdout, lines), daemon=True)
    reader.start()
    captured: list[str] = []
    try:
        while proc.poll() is None:
            if _cancelled(cancel_file):
                _terminate_process_group(proc)
                raise KeyboardInterrupt()
            try:
                line = lines.get(timeout=0.2)
            except queue.Empty:
                continue
            if line is None:
                continue
            captured.append(line.rstrip()[:1000])
            print(line, end="", flush=True)
        rc = proc.wait()
        drain_deadline = time.monotonic() + 1.0
        while time.monotonic() < drain_deadline:
            try:
                line = lines.get(timeout=0.05)
            except queue.Empty:
                break
            if line is None:
                break
            captured.append(line.rstrip()[:1000])
            print(line, end="", flush=True)
        if _cancelled(cancel_file):
            raise KeyboardInterrupt()
        output = "\n".join(captured)
        if rc != 0:
            error = RuntimeError(f"{error_label} exited {rc}: {_sanitize_error_tail(output)}")
            error.child_output = output
            error.returncode = rc
            raise error
        return output
    finally:
        if proc.poll() is None:
            _terminate_process_group(proc)
        reader.join(timeout=1)


def _run_reel_cover_child(
    cmd: list[str], cwd: Path, cancel_file: Path, *, env: dict | None = None,
) -> None:
    try:
        _run_cpu_child(
            cmd, cwd, cancel_file, env=env,
            error_label="reel cover generator")
    except RuntimeError as error:
        output = getattr(error, "child_output", str(error))
        marker = _reel_cover_billing_marker(output)
        if marker:
            raise RuntimeError(f"billing_block: {marker}: {_sanitize_error_tail(output)}") from error
        raise


def _reel_ref_payload(value, field: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an input ref object")
    kind = str(value.get("kind") or "").strip()
    if kind == "artifact":
        allowed = {"kind", "artifact_id", "content_hash"}
        unknown = sorted(key for key in value if key not in allowed)
        if unknown:
            raise ValueError(f"{field} unknown ref keys: {', '.join(unknown)}")
        artifact_id = str(value.get("artifact_id") or "").strip()
        content_hash = str(value.get("content_hash") or "").strip()
        if not artifact_id or not content_hash:
            raise ValueError(f"{field} artifact ref requires artifact_id and content_hash")
        return {"kind": kind, "artifact_id": artifact_id, "content_hash": content_hash}
    if kind == "path":
        allowed = {"kind", "path", "content_hash"}
        unknown = sorted(key for key in value if key not in allowed)
        if unknown:
            raise ValueError(f"{field} unknown ref keys: {', '.join(unknown)}")
        path = str(value.get("path") or "").strip()
        content_hash = str(value.get("content_hash") or "").strip()
        if not path or not content_hash:
            raise ValueError(f"{field} path ref requires path and content_hash")
        return {"kind": kind, "path": path, "content_hash": content_hash}
    raise ValueError(f"{field} ref kind must be artifact or path")


def _resolve_reel_input_ref(ref: dict, field: str) -> dict:
    if ref["kind"] == "path":
        path = _safe_existing_input_path(ref["path"], field)
        actual_hash = _file_sha256(path)
        if actual_hash != ref["content_hash"]:
            raise ValueError(f"{field} content_hash mismatch")
        return {
            **ref,
            "path": path,
            "basename": path.name,
            "content_hash": actual_hash,
        }
    with connect() as conn:
        row = conn.execute("select * from artifacts where id = ?", (ref["artifact_id"],)).fetchone()
        if row is None:
            raise ValueError(f"{field} artifact not found")
        row_hash = str(row["content_hash"] or "")
        if row_hash != ref["content_hash"]:
            raise ValueError(f"{field} artifact content_hash mismatch")
        metadata = json.loads(row["metadata_json"] or "{}")
        path = artifact_abs_path(conn, row)
    if path is None or not path.is_file():
        raise ValueError(f"{field} artifact file is missing")
    if not _path_is_allowed(path, _task_allowed_roots()):
        raise ValueError(f"{field} artifact file is outside allowed orchestrator roots")
    actual_hash = _file_sha256(path)
    if actual_hash != ref["content_hash"]:
        raise ValueError(f"{field} artifact file content_hash mismatch")
    return {
        **ref,
        "path": path,
        "basename": path.name,
        "content_hash": actual_hash,
        "metadata": metadata if isinstance(metadata, dict) else {},
    }


@contextmanager
def _reel_workspace_lock(source_root: Path):
    """Serialize legacy-root materialization and execution.

    The hardened backends still use fixed paths below video-mvp.  Until they
    each accept a task-owned work root, every native stage restores immutable
    Castle snapshots and executes while holding this lock.  This makes the
    compatibility boundary correct even when multiple orchestrator workers are
    running.
    """
    lock_dir = source_root / ".castle-native"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / "workspace.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(lock_path, flags, 0o600)
    except OSError as error:
        raise RuntimeError("could not open Reel workspace lock") from error
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise RuntimeError("Reel workspace lock must be a regular file")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


@contextmanager
def _reel_host_audio_authority_lock(
    source_root: Path,
    episode: str,
    *,
    exclusive: bool,
):
    """Coordinate Castle restores/readers with clone_host's authority writer."""
    if not re.fullmatch(r"[0-9]+", episode):
        raise ValueError("host audio authority lock episode must contain digits only")
    audio_root = source_root / f"audio_host_{episode}"
    audio_root.mkdir(parents=True, exist_ok=True)
    try:
        root_mode = audio_root.lstat().st_mode
    except FileNotFoundError as error:
        raise RuntimeError("host audio authority root disappeared") from error
    if audio_root.is_symlink() or not stat.S_ISDIR(root_mode):
        raise RuntimeError(
            "host audio authority root must be a non-symlink directory")
    lock_path = audio_root / ".host_audio_writer.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(lock_path, flags, 0o600)
    except OSError as error:
        raise RuntimeError("could not open host audio authority lock") from error
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise RuntimeError("host audio authority lock must be a regular file")
        fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _safe_bundle_relative(value: str, field: str = "bundle path") -> str:
    text = str(value or "").strip().replace("\\", "/")
    path = Path(text)
    if (
        not text
        or path.is_absolute()
        or ".." in path.parts
        or any(part in {"", "."} for part in path.parts)
        or text.startswith("~")
    ):
        raise ValueError(f"{field} must be a safe relative path")
    return "/".join(path.parts)


def _tar_info(name: str, size: int, *, mode: int = 0o644) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size = size
    info.mode = mode
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    return info


@contextmanager
def _open_reel_snapshot_source(source_root: Path, relative: str):
    """Open a bundle member beneath source_root without following symlinks.

    Resolve/check-then-open is not sufficient here: another process can replace
    either a parent directory or the final file between those operations.  Walk
    the relative path using directory file descriptors and O_NOFOLLOW, then keep
    the final descriptor open while its bytes are read.
    """
    relative = _safe_bundle_relative(relative)
    parts = Path(relative).parts
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    file_flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    opened_directories: list[int] = []
    file_fd = None
    handle = None
    try:
        root_fd = os.open(source_root, directory_flags)
        opened_directories.append(root_fd)
        parent_fd = root_fd
        for part in parts[:-1]:
            parent_fd = os.open(part, directory_flags, dir_fd=parent_fd)
            opened_directories.append(parent_fd)
        file_fd = os.open(parts[-1], file_flags, dir_fd=parent_fd)
        info = os.fstat(file_fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(
                f"Reel snapshot source must be a regular file: {Path(relative).name}")
        handle = os.fdopen(file_fd, "rb", closefd=True)
        file_fd = None
        yield handle
    except OSError as error:
        raise ValueError(
            f"Reel snapshot source must be a regular file: {Path(relative).name}") from error
    finally:
        if handle is not None:
            handle.close()
        if file_fd is not None:
            os.close(file_fd)
        for fd in reversed(opened_directories):
            os.close(fd)


def _stable_open_file_identity(handle, *, label: str) -> tuple[int, str]:
    before = os.fstat(handle.fileno())
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"{label} must be a regular file")
    handle.seek(0)
    digest = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        size += len(chunk)
        digest.update(chunk)
    after = os.fstat(handle.fileno())
    stable_fields = (
        "st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns",
    )
    if (
        any(getattr(before, key) != getattr(after, key) for key in stable_fields)
        or size != before.st_size
    ):
        raise RuntimeError(f"{label} changed while hashing")
    return size, digest.hexdigest()


class _HashingReelSnapshotReader:
    def __init__(self, handle):
        self.handle = handle
        self.size = 0
        self.digest = hashlib.sha256()

    def read(self, amount: int = -1) -> bytes:
        data = self.handle.read(amount)
        self.size += len(data)
        self.digest.update(data)
        return data


def _require_expected_bundle_identities(
    rows: list[dict],
    expected_identities: dict[str, dict] | None,
) -> None:
    if not expected_identities:
        return
    by_path = {row["path"]: row for row in rows}
    for raw_relative, expected in expected_identities.items():
        relative = _safe_bundle_relative(raw_relative, "expected bundle path")
        row = by_path.get(relative)
        if (
            row is None
            or not isinstance(expected, dict)
            or row["size"] != expected.get("size")
            or row["sha256"] != expected.get("sha256")
        ):
            raise RuntimeError(
                f"Reel snapshot differs from expected identity: {Path(relative).name}")


def _write_reel_snapshot_bundle(
    source_root: Path,
    relative_paths: list[str] | tuple[str, ...] | set[str],
    destination: Path,
    *,
    episode: str,
    stage: str,
    theme: str | None = None,
    expected_identities: dict[str, dict] | None = None,
) -> dict:
    rows = []
    seen = set()
    for raw in sorted(str(item) for item in relative_paths):
        relative = _safe_bundle_relative(raw)
        if relative == ".castle-bundle.json" or relative in seen:
            raise ValueError("Reel snapshot has a duplicate or reserved path")
        seen.add(relative)
        with _open_reel_snapshot_source(source_root, relative) as handle:
            size, digest = _stable_open_file_identity(
                handle, label=f"Reel snapshot source {Path(relative).name}")
        rows.append({
            "path": relative,
            "size": size,
            "sha256": digest,
        })
    if not rows:
        raise ValueError("Reel snapshot must contain at least one file")
    _require_expected_bundle_identities(rows, expected_identities)
    manifest = {
        "schema_version": REEL_NATIVE_BUNDLE_SCHEMA_VERSION,
        "episode": episode,
        "stage": stage,
        "files": rows,
    }
    if theme is not None:
        manifest["theme"] = theme
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}-{time.time_ns()}")
    try:
        with tarfile.open(temporary, "w", format=tarfile.PAX_FORMAT) as archive:
            archive.addfile(_tar_info(".castle-bundle.json", len(manifest_bytes)), io.BytesIO(manifest_bytes))
            for row in rows:
                with _open_reel_snapshot_source(source_root, row["path"]) as handle:
                    before = os.fstat(handle.fileno())
                    reader = _HashingReelSnapshotReader(handle)
                    archive.addfile(_tar_info(row["path"], row["size"]), reader)
                    after = os.fstat(handle.fileno())
                    stable_fields = (
                        "st_dev", "st_ino", "st_mode", "st_size",
                        "st_mtime_ns", "st_ctime_ns",
                    )
                    if (
                        any(getattr(before, key) != getattr(after, key)
                            for key in stable_fields)
                        or reader.size != row["size"]
                        or reader.digest.hexdigest() != row["sha256"]
                    ):
                        raise RuntimeError(
                            f"Reel snapshot source changed while packaging: "
                            f"{Path(row['path']).name}")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return manifest


def _extract_reel_snapshot_bundle(
    ref,
    source_root: Path,
    field: str,
    *,
    expected_episode: str,
    expected_stage: str | None = None,
    expected_theme: str | None = None,
) -> list[str]:
    if not re.fullmatch(r"[0-9]+", expected_episode):
        raise ValueError(f"{field} expected episode must contain digits only")
    resolved = _resolve_reel_input_ref(_reel_ref_payload(ref, field), field)
    producer_episode = resolved.get("metadata", {}).get("episode")
    if resolved["kind"] == "artifact" and (
        str(producer_episode) != expected_episode
    ):
        raise ValueError(f"{field} producer episode mismatch")
    if expected_stage is not None:
        metadata = resolved.get("metadata", {})
        if (
            resolved["kind"] != "artifact"
            or metadata.get("workflow") != "castle.reel_native_stage"
            or metadata.get("role") != "stage_bundle"
            or metadata.get("stage") != expected_stage
            or metadata.get("theme") != expected_theme
        ):
            raise ValueError(f"{field} has the wrong native stage producer")
    bundle = resolved["path"]
    try:
        archive = tarfile.open(bundle, "r:*")
    except (OSError, tarfile.TarError) as error:
        raise ValueError(f"{field} is not a valid Reel snapshot bundle") from error
    with archive:
        members = archive.getmembers()
        if len(members) < 2 or len(members) > 4096:
            raise ValueError(f"{field} has an invalid member count")
        by_name = {}
        for member in members:
            name = _safe_bundle_relative(member.name, f"{field} member")
            if name in by_name or not member.isreg() or member.issym() or member.islnk():
                raise ValueError(f"{field} contains duplicate or non-regular members")
            by_name[name] = member
        manifest_member = by_name.get(".castle-bundle.json")
        if manifest_member is None or manifest_member.size > 1024 * 1024:
            raise ValueError(f"{field} is missing its bounded bundle manifest")
        manifest_stream = archive.extractfile(manifest_member)
        if manifest_stream is None:
            raise ValueError(f"{field} bundle manifest is unreadable")
        try:
            manifest = json.loads(manifest_stream.read().decode("utf-8"))
        except Exception as error:
            raise ValueError(f"{field} bundle manifest is invalid JSON") from error
        if not isinstance(manifest, dict) or manifest.get("schema_version") != REEL_NATIVE_BUNDLE_SCHEMA_VERSION:
            raise ValueError(f"{field} bundle manifest schema is unsupported")
        if str(manifest.get("episode")) != expected_episode:
            raise ValueError(f"{field} bundle manifest episode mismatch")
        if expected_stage is not None and (
            manifest.get("stage") != expected_stage
            or manifest.get("theme") != expected_theme
        ):
            raise ValueError(f"{field} bundle stage identity mismatch")
        rows = manifest.get("files")
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"{field} bundle manifest files must be non-empty")
        declared = {}
        total = 0
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or set(row) != {"path", "size", "sha256"}:
                raise ValueError(f"{field} bundle file {index} is malformed")
            name = _safe_bundle_relative(row.get("path"), f"{field} bundle file")
            host_did = re.search(
                r"(?:^|/)audio_host_([0-9]+)(?:/|$)", name)
            if host_did and host_did.group(1) != expected_episode:
                raise ValueError(
                    f"{field} bundle contains cross-episode host audio")
            size = row.get("size")
            digest = str(row.get("sha256") or "")
            if (
                name in declared
                or not isinstance(size, int)
                or size < 0
                or not re.fullmatch(r"[0-9a-f]{64}", digest)
            ):
                raise ValueError(f"{field} bundle file {index} has invalid identity")
            declared[name] = {"size": size, "sha256": digest}
            total += size
        if total > 20 * 1024 * 1024 * 1024:
            raise ValueError(f"{field} bundle exceeds the 20 GiB safety bound")
        if set(by_name) != set(declared) | {".castle-bundle.json"}:
            raise ValueError(f"{field} bundle members do not match its manifest")

        restored = []
        root = source_root.resolve()
        for name in sorted(declared):
            member = by_name[name]
            expected = declared[name]
            if member.size != expected["size"]:
                raise ValueError(f"{field} member size mismatch: {Path(name).name}")
            target = source_root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            resolved_parent = target.parent.resolve()
            try:
                resolved_parent.relative_to(root)
            except ValueError as error:
                raise ValueError(f"{field} member parent escapes source root") from error
            if target.exists() and target.is_symlink():
                raise ValueError(f"{field} refuses to replace a symlink")
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError(f"{field} member is unreadable")
            temp = target.with_name(f".{target.name}.castle-tmp-{os.getpid()}-{time.time_ns()}")
            digest = hashlib.sha256()
            size = 0
            try:
                with temp.open("xb") as handle:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        size += len(chunk)
                        if size > expected["size"]:
                            raise ValueError(f"{field} member exceeded declared size")
                        digest.update(chunk)
                        handle.write(chunk)
                    handle.flush()
                    os.fsync(handle.fileno())
                if size != expected["size"] or digest.hexdigest() != expected["sha256"]:
                    raise ValueError(f"{field} member digest mismatch: {Path(name).name}")
                os.replace(temp, target)
            finally:
                temp.unlink(missing_ok=True)
            restored.append(name)
        return restored


def _stable_path_identity(path: Path, *, label: str) -> tuple[int, str]:
    with _open_reel_snapshot_source(path.parent, path.name) as handle:
        return _stable_open_file_identity(handle, label=label)


def _snapshot_task_output(
    source: Path,
    work_dir: Path,
    index: int,
    name: str,
    *,
    expected_size: int | None = None,
    expected_sha256: str | None = None,
) -> Path:
    if (expected_size is None) != (expected_sha256 is None):
        raise ValueError("Reel task snapshot expected identity is incomplete")
    source_size, source_sha256 = _stable_path_identity(
        source, label=f"Reel task output {source.name}")
    if expected_size is None:
        expected_size = source_size
        expected_sha256 = source_sha256
    elif source_size != expected_size or source_sha256 != expected_sha256:
        raise RuntimeError("Reel task output differs from its expected identity")
    safe = _safe_output_name(name, f"output-{index}.bin")
    destination = work_dir / "tmp" / "snapshots" / f"{index:04d}-{safe}"
    _atomic_copy(source, destination)
    destination_size, destination_sha256 = _stable_path_identity(
        destination, label=f"Reel task snapshot {destination.name}")
    if (
        destination_size != expected_size
        or destination_sha256 != expected_sha256
    ):
        raise RuntimeError("Reel task snapshot differs from its expected output")
    return destination


def _snapshot_output_specs(outputs: list[dict], work_dir: Path) -> list[dict]:
    work_root = work_dir.resolve()
    for index, output in enumerate(outputs):
        source = Path(str(output.get("path") or "")).expanduser().resolve()
        try:
            source.relative_to(work_root)
            continue
        except ValueError:
            pass
        snapshot = _snapshot_task_output(
            source,
            work_dir,
            index,
            str(output.get("name") or source.name),
        )
        output["path"] = str(snapshot)
    return outputs


def _configured_reel_source_root(value=None) -> Path:
    configured = _trusted_existing_path(
        "CASTLE_REEL5_SOURCE_ROOT",
        Path.home() / ".local/share/web-comfyui/integrations/voice/video-mvp",
        "reel_source_root",
        file=False,
    ).resolve()
    if value not in (None, ""):
        supplied = Path(str(value)).expanduser().resolve()
        if supplied != configured:
            raise ValueError("source_root must match CASTLE_REEL5_SOURCE_ROOT")
    return configured


def _configured_reel_runtime_lock() -> dict:
    path = _trusted_existing_path(
        "CASTLE_REEL_RUNTIME_LOCK",
        WEBUI_ROOT.parent / "runtime/reel/reel-runtime.lock.json",
        "reel_runtime_lock",
    )
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except Exception as error:
        raise ValueError("reel runtime lock is not valid JSON") from error
    if not isinstance(value, dict) or value.get("schema_version") != "castle-reel-runtime-lock-v1":
        raise ValueError("reel runtime lock schema is invalid")
    bundle_id = str(value.get("bundle_id") or "").strip()
    content_set_sha256 = str(value.get("content_set_sha256") or "").strip()
    entry_count = value.get("entry_count")
    total_bytes = value.get("total_bytes")
    if (
        not bundle_id
        or not re.fullmatch(r"[0-9a-f]{64}", content_set_sha256)
        or not isinstance(entry_count, int)
        or entry_count < 1
        or not isinstance(total_bytes, int)
        or total_bytes < 1
    ):
        raise ValueError("reel runtime lock identity is incomplete")
    groups = value.get("groups")
    entries = value.get("entries")
    if not isinstance(groups, dict) or not isinstance(entries, list):
        raise ValueError("reel runtime lock groups or entries are missing")
    if len(entries) != entry_count:
        raise ValueError("reel runtime lock entry_count differs from entries")
    if sum(
        entry.get("bytes", -1) if isinstance(entry, dict) and isinstance(entry.get("bytes"), int) else -1
        for entry in entries
    ) != total_bytes:
        raise ValueError("reel runtime lock total_bytes differs from entries")
    for group in REEL_RUNTIME_GROUPS:
        declared = groups.get(group)
        selected = [
            entry for entry in entries
            if isinstance(entry, dict) and entry.get("group") == group
        ]
        if not isinstance(declared, dict) or not selected:
            raise ValueError(f"reel runtime lock group is missing: {group}")
        identity = [{
            "runtime_path": entry.get("runtime_path"),
            "bytes": entry.get("bytes"),
            "sha256": entry.get("sha256"),
            "required": entry.get("required"),
        } for entry in sorted(selected, key=lambda item: str(item.get("runtime_path") or ""))]
        if (
            declared.get("entry_count") != len(selected)
            or declared.get("total_bytes") != sum(int(entry.get("bytes") or 0) for entry in selected)
            or declared.get("content_sha256") != input_hash(identity)
        ):
            raise ValueError(f"reel runtime lock group identity differs from entries: {group}")
    scene_entries = [
        entry for entry in entries
        if isinstance(entry, dict) and entry.get("group") == "scene_assets"
    ]
    expected_scene_paths = {
        f"tachie/scenes/{theme}.png" for theme in REEL_CANONICAL_THEMES
    }
    if (
        {str(entry.get("runtime_path") or "") for entry in scene_entries}
        != expected_scene_paths
        or any(
            entry.get("id") != f"scene.{Path(str(entry.get('runtime_path') or '')).stem}"
            or entry.get("role") != "scene_background"
            or entry.get("source") != {
                "root": "video_mvp", "path": entry.get("runtime_path"),
            }
            or not isinstance(entry.get("bytes"), int)
            or entry["bytes"] < 1
            or not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256") or ""))
            or entry.get("required") is not True
            or entry.get("sensitive") is not False
            for entry in scene_entries
        )
    ):
        raise ValueError("reel runtime scene collection is not the exact 20-theme set")
    return {
        "path": path,
        "bundle_id": bundle_id,
        "content_set_sha256": content_set_sha256,
        "entry_count": entry_count,
        "total_bytes": total_bytes,
        "lock_sha256": hashlib.sha256(raw).hexdigest(),
        "manifest": value,
    }


def _reel_runtime_group_document(locked: dict, group: str) -> dict:
    if group not in REEL_RUNTIME_GROUPS:
        raise ValueError(f"unsupported Reel runtime group: {group}")
    manifest = locked["manifest"]
    declared = manifest["groups"][group]
    entries = []
    for entry in sorted(
        (item for item in manifest["entries"] if item.get("group") == group),
        key=lambda item: item["runtime_path"],
    ):
        entries.append({
            "id": entry["id"],
            "role": entry["role"],
            "runtime_path": entry["runtime_path"],
            "source": {
                "root": entry["source"]["root"],
                "path": entry["source"]["path"],
            },
            "bytes": entry["bytes"],
            "sha256": entry["sha256"],
            "required": entry["required"],
        })
    document = {
        "schema_version": REEL_RUNTIME_GROUP_LOCK_SCHEMA_VERSION,
        "bundle_id": locked["bundle_id"],
        "content_set_sha256": locked["content_set_sha256"],
        "lock_sha256": locked["lock_sha256"],
        "group": group,
        "group_content_sha256": declared["content_sha256"],
        "entry_count": declared["entry_count"],
        "total_bytes": declared["total_bytes"],
        "entries": entries,
    }
    cache_rule = manifest.get("source_roots", {}).get("breezyvoice_cache", {})
    revision = str(cache_rule.get("revision") or "").strip()
    if revision:
        document["breezyvoice_revision"] = revision
    return document


def _reel_runtime_source_roots(source_root: Path) -> dict[str, Path]:
    whisper_cache = Path(os.environ.get(
        "CASTLE_REEL_WHISPER_CACHE_ROOT",
        Path.home() / ".cache/whisper",
    )).expanduser().resolve()
    if whisper_cache.name != "whisper":
        raise ValueError(
            "CASTLE_REEL_WHISPER_CACHE_ROOT must end in /whisper so OpenAI Whisper consumes it")
    return {
        "video_mvp": source_root.resolve(),
        "voice_lab": source_root.resolve().parent,
        "breezyvoice_cache": Path(os.environ.get(
            "CASTLE_REEL_BREEZYVOICE_CACHE_ROOT",
            Path.home() / ".cache/huggingface/hub/models--MediaTek-Research--BreezyVoice-300M",
        )).expanduser().resolve(),
        "whisper_cache": whisper_cache,
    }


def _verify_reel_runtime_group_sources(document: dict, source_root: Path) -> list[str]:
    roots = _reel_runtime_source_roots(source_root)
    verified = []
    for entry in document["entries"]:
        source = entry["source"]
        root = roots.get(source["root"])
        if root is None or root.is_symlink() or not root.is_dir():
            raise ValueError(f"runtime group source root is unavailable: {source['root']}")
        relative = _safe_bundle_relative(source["path"], "runtime group source path")
        candidate = root.joinpath(*Path(relative).parts)
        if candidate.is_symlink():
            raise ValueError("runtime group source is a symlink")
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError) as error:
            raise ValueError("runtime group source escapes its root") from error
        before = resolved.stat()
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size != entry["bytes"]
            or _file_sha256(resolved) != entry["sha256"]
        ):
            raise ValueError(f"runtime group source drifted: {Path(relative).name}")
        after = resolved.stat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        ):
            raise ValueError("runtime group source changed during verification")
        verified.append(resolved.relative_to(source_root).as_posix() if source["root"] == "video_mvp" else "")
    closed_world = []
    if document["group"] == "sfx_sources":
        closed_world.append(("cheer_src", "*.mp3"))
    elif document["group"] == "music":
        closed_world.append(("music/mureka", "*.mp3"))
    for directory, pattern in closed_world:
        expected = {
            entry["source"]["path"]
            for entry in document["entries"]
            if entry["source"]["root"] == "video_mvp"
            and Path(entry["source"]["path"]).parent.as_posix() == directory
            and Path(entry["source"]["path"]).match(f"{directory}/{pattern}")
        }
        root = source_root / directory
        if root.is_symlink() or not root.is_dir():
            raise ValueError(f"locked runtime directory is unavailable: {directory}")
        candidates = list(root.glob(pattern))
        if any(path.is_symlink() or not path.is_file() for path in candidates):
            raise ValueError(f"locked runtime directory contains an unsafe file: {directory}")
        actual = {path.relative_to(source_root).as_posix() for path in candidates}
        if actual != expected:
            raise ValueError(f"locked runtime directory has untracked files: {directory}")
    cache_entries = [
        entry for entry in document["entries"]
        if entry["source"]["root"] == "breezyvoice_cache"
    ]
    if cache_entries:
        revision = str(document.get("breezyvoice_revision") or "").strip()
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("locked BreezyVoice revision is missing")
        cache_root = roots["breezyvoice_cache"]
        main_ref = cache_root / "refs/main"
        if main_ref.is_symlink() or not main_ref.is_file() or main_ref.read_text(encoding="utf-8").strip() != revision:
            raise ValueError("BreezyVoice refs/main differs from the locked revision")
        snapshot = cache_root / "snapshots" / revision
        if snapshot.is_symlink() or not snapshot.is_dir():
            raise ValueError("locked BreezyVoice snapshot is unavailable")
        for entry in cache_entries:
            blob = cache_root.joinpath(*Path(entry["source"]["path"]).parts).resolve(strict=True)
            snapshot_file = snapshot / Path(entry["runtime_path"]).name
            if not snapshot_file.is_symlink() or snapshot_file.resolve(strict=True) != blob:
                raise ValueError(
                    f"BreezyVoice snapshot mapping drifted: {snapshot_file.name}")
        host_model = source_root / "host/host_model"
        if host_model.is_symlink() or not host_model.is_dir():
            raise ValueError("operator host model directory is unavailable")
        host_mapping_entries = [
            entry for entry in cache_entries
            if Path(entry["runtime_path"]).parent.as_posix() == "host/host_model"
        ]
        expected_host_names = {
            Path(entry["runtime_path"]).name
            for entry in document["entries"]
            if Path(entry["runtime_path"]).parent.as_posix() == "host/host_model"
        }
        if {path.name for path in host_model.iterdir()} != expected_host_names:
            raise ValueError("operator host model directory has untracked entries")
        for entry in host_mapping_entries:
            path = source_root / entry["runtime_path"]
            blob = cache_root.joinpath(*Path(entry["source"]["path"]).parts).resolve(strict=True)
            if not path.is_symlink() or path.resolve(strict=True) != blob:
                raise ValueError(f"operator host model mapping drifted: {path.name}")
    return [item for item in verified if item]


def _require_reel_runtime_group_ref(value, group: str, source_root: Path, *, verify_source: bool) -> tuple[dict, dict]:
    field = f"runtime_{group}_lock"
    ref = _resolve_reel_input_ref(_reel_ref_payload(value, field), field)
    if ref["kind"] != "artifact":
        raise ValueError(f"{field} must be an adopted Castle artifact")
    metadata = ref.get("metadata", {})
    if (
        metadata.get("workflow") != "castle.reel_runtime_verify"
        or metadata.get("role") != field
        or metadata.get("group") != group
    ):
        raise ValueError(f"{field} has the wrong Castle producer")
    try:
        document = json.loads(ref["path"].read_text(encoding="utf-8"))
    except Exception as error:
        raise ValueError(f"{field} is not valid JSON") from error
    expected = _reel_runtime_group_document(_configured_reel_runtime_lock(), group)
    if document != expected:
        raise ValueError(f"{field} differs from the configured runtime lock")
    for key in (
        "bundle_id", "content_set_sha256", "lock_sha256", "group",
        "group_content_sha256", "entry_count", "total_bytes",
    ):
        if metadata.get(key) != expected.get(key):
            raise ValueError(f"{field} metadata differs from its body")
    if verify_source:
        _verify_reel_runtime_group_sources(document, source_root)
    return ref, document


def _locked_reel_scene_entry(document: dict, theme: str) -> dict:
    if theme not in REEL_CANONICAL_THEMES:
        raise ValueError("scene stage theme must be a canonical Reel category")
    relative = f"tachie/scenes/{theme}.png"
    matches = [
        entry for entry in document.get("entries", [])
        if isinstance(entry, dict)
        and entry.get("runtime_path") == relative
        and entry.get("source") == {"root": "video_mvp", "path": relative}
    ]
    if (
        document.get("schema_version") != REEL_RUNTIME_GROUP_LOCK_SCHEMA_VERSION
        or document.get("group") != "scene_assets"
        or len(matches) != 1
        or not isinstance(matches[0].get("bytes"), int)
        or matches[0]["bytes"] < 1
        or not re.fullmatch(r"[0-9a-f]{64}", str(matches[0].get("sha256") or ""))
    ):
        raise ValueError("scene_assets lock is missing the selected scene identity")
    return matches[0]


def _require_locked_reel_scene_source(
    source_root: Path,
    document: dict,
    theme: str,
) -> dict:
    entry = _locked_reel_scene_entry(document, theme)
    relative = entry["runtime_path"]
    with _open_reel_snapshot_source(source_root, relative) as handle:
        size, digest = _stable_open_file_identity(
            handle, label=f"locked Reel scene {Path(relative).name}")
    if size != entry["bytes"] or digest != entry["sha256"]:
        raise ValueError("selected Reel scene differs from the locked runtime")
    return entry


def _require_native_scene_row(
    rows: list[dict],
    document: dict,
    theme: str,
) -> dict:
    entry = _locked_reel_scene_entry(document, theme)
    expected_path = entry["runtime_path"]
    if (
        len(rows) != 1
        or rows[0].get("name") != "scene"
        or rows[0].get("path") != expected_path
        or rows[0].get("size") != entry["bytes"]
        or rows[0].get("sha256") != entry["sha256"]
    ):
        raise RuntimeError("native Reel scene output differs from the locked runtime")
    return entry


def run_castle_reel_runtime_verify(payload: dict, work_dir: Path, cancel_file: Path) -> list[dict]:
    allowed = {"episode", "source_root"}
    unknown = sorted(key for key in payload if not str(key).startswith("_") and key not in allowed)
    if unknown:
        raise ValueError(f"castle.reel_runtime_verify unknown fields: {', '.join(unknown)}")
    source_root = _configured_reel_source_root(payload.get("source_root"))
    manager = _trusted_existing_path(
        "CASTLE_REEL_RUNTIME_MANAGER",
        WEBUI_ROOT.parent / "scripts/manage_reel_runtime_bundle.py",
        "reel_runtime_manager",
    )
    locked = _configured_reel_runtime_lock()
    manager_sha256 = _file_sha256(manager)
    runtime_roots = _reel_runtime_source_roots(source_root)
    def invoke(command_name: str) -> dict:
        command = [
            sys.executable,
            str(manager),
            command_name,
            "--lock", str(locked["path"]),
            "--source-root", str(source_root),
            "--breezyvoice-cache-root", str(runtime_roots["breezyvoice_cache"]),
            "--whisper-cache-root", str(runtime_roots["whisper_cache"]),
            "--json",
        ]
        output = _run_cpu_child(
            command,
            manager.parent,
            cancel_file,
            error_label=f"Reel runtime source audit {command_name}",
        )
        line = next((item.strip() for item in reversed(output.splitlines()) if item.strip()), "")
        try:
            result = json.loads(line)
        except Exception as error:
            raise RuntimeError("Reel runtime source audit returned invalid JSON") from error
        if (
            not isinstance(result, dict)
            or result.get("ok") is not True
            or result.get("command") != command_name
        ):
            raise RuntimeError(f"Reel runtime source audit {command_name} is not healthy")
        if (
            result.get("bundle_id") != locked["bundle_id"]
            or result.get("content_set_sha256") != locked["content_set_sha256"]
        ):
            raise RuntimeError(f"Reel runtime source audit {command_name} identity differs from lock")
        return result

    source_audit = invoke("audit-source")
    if (
        source_audit.get("entry_count") != locked["entry_count"]
        or source_audit.get("total_bytes") != locked["total_bytes"]
        or source_audit.get("writes_performed") != 0
        or source_audit.get("mode") != "deep"
    ):
        raise RuntimeError("Reel runtime source audit differs from lock")
    receipt = {
        "schema_version": "castle-reel-runtime-source-receipt-v1",
        "bundle_id": str(source_audit.get("bundle_id") or ""),
        "content_set_sha256": str(source_audit.get("content_set_sha256") or ""),
        "source_ok": source_audit.get("ok") is True,
        "mode": "source-deep",
        "entry_count": locked["entry_count"],
        "total_bytes": locked["total_bytes"],
        "lock_sha256": locked["lock_sha256"],
        "manager_sha256": manager_sha256,
    }
    if (
        not receipt["bundle_id"]
        or not re.fullmatch(r"[0-9a-f]{64}", receipt["content_set_sha256"])
        or not receipt["source_ok"]
        or receipt["mode"] != "source-deep"
    ):
        raise RuntimeError("Reel runtime source receipt is incomplete")
    path = work_dir / "tmp" / "reel-runtime-receipt.json"
    _write_text(path, json.dumps(receipt, sort_keys=True, indent=2) + "\n")
    outputs = [{
        "path": str(path),
        "name": path.name,
        "artifact_type": "json",
        "output_name": "runtime_receipt",
        "metadata": {
            "workflow": "castle.reel_runtime_verify",
            "role": "runtime_receipt",
            **receipt,
        },
        "tags": ["project:ai-sister", "workflow:reel5", "role:runtime_assets"],
    }]
    for group in REEL_RUNTIME_GROUPS:
        document = _reel_runtime_group_document(locked, group)
        role = f"runtime_{group}_lock"
        group_path = work_dir / "tmp" / f"{role}.json"
        _write_text(group_path, json.dumps(document, sort_keys=True, indent=2) + "\n")
        outputs.append({
            "path": str(group_path),
            "name": group_path.name,
            "artifact_type": "json",
            "output_name": role,
            "metadata": {
                "workflow": "castle.reel_runtime_verify",
                "role": role,
                **{key: document[key] for key in (
                    "bundle_id", "content_set_sha256", "lock_sha256", "group",
                    "group_content_sha256", "entry_count", "total_bytes",
                )},
            },
            "tags": [
                "project:ai-sister", "workflow:reel5", "role:runtime_assets",
                f"runtime_group:{group}",
            ],
        })
    return outputs


def _native_stage_result(path: Path, source_root: Path, *, stage: str, episode: str) -> list[dict]:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        raise RuntimeError("native Reel stage result is invalid JSON") from error
    if (
        not isinstance(result, dict)
        or result.get("schema_version") != "castle-reel-native-stage-result-v1"
        or result.get("stage") != stage
        or str(result.get("episode") or "") != episode
        or not isinstance(result.get("outputs"), list)
        or not result["outputs"]
    ):
        raise RuntimeError("native Reel stage result contract mismatch")
    clean = []
    seen_names = set()
    seen_paths = set()
    for index, row in enumerate(result["outputs"]):
        if not isinstance(row, dict) or set(row) != {"name", "path", "sha256", "size"}:
            raise RuntimeError(f"native Reel stage output {index} is malformed")
        name = str(row.get("name") or "")
        relative = _safe_bundle_relative(row.get("path"), "native stage output path")
        digest = str(row.get("sha256") or "")
        size = row.get("size")
        if (
            not re.fullmatch(r"[A-Za-z0-9._-]{1,120}", name)
            or name in seen_names
            or relative in seen_paths
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or not isinstance(size, int)
            or size < 0
        ):
            raise RuntimeError(f"native Reel stage output {index} has invalid identity")
        source = (source_root / relative).resolve()
        try:
            source.relative_to(source_root.resolve())
        except ValueError as error:
            raise RuntimeError("native Reel stage output escapes source root") from error
        if source.is_symlink() or not source.is_file():
            raise RuntimeError("native Reel stage output is missing or not regular")
        if source.stat().st_size != size or _file_sha256(source) != digest:
            raise RuntimeError("native Reel stage output bytes do not match its result")
        seen_names.add(name)
        seen_paths.add(relative)
        clean.append({**row, "name": name, "path": relative, "source": source})
    return clean


def run_castle_reel_native_stage(
    payload: dict,
    work_dir: Path,
    cancel_file: Path,
    *,
    expected_lane: str,
) -> list[dict]:
    allowed = {
        "stage", "episode", "source_root", "theme", "final_input",
        "implementation_hash", "approved_script", "listener_rejection_ledger",
        "main_audio_resynth_idxs", "main_audio_cache_revision",
        "host_reaction_bank_rejections", "qc_host_reaction_reports",
        *(f"runtime_{group}_lock" for group in REEL_RUNTIME_GROUPS),
    }
    unknown = sorted(
        key for key in payload
        if not str(key).startswith("_")
        and key not in allowed
        and not str(key).startswith("input_")
    )
    if unknown:
        raise ValueError(f"castle.reel_stage_{expected_lane} unknown fields: {', '.join(unknown)}")
    stage = str(payload.get("stage") or "").strip()
    task_type = f"castle.reel_stage_{expected_lane}"
    if stage not in REEL_NATIVE_STAGE_TASK_TYPES.get(task_type, set()):
        raise ValueError(f"{task_type} rejects stage {stage or '[empty]'}")
    main_audio_resynth_idxs = _reel_main_audio_resynth_idxs(payload)
    main_audio_cache_revision = _reel_main_audio_cache_revision(
        payload, required=bool(main_audio_resynth_idxs))
    if main_audio_resynth_idxs and stage != "synth":
        raise ValueError("main_audio_resynth_idxs is only valid for synth")
    if main_audio_cache_revision and not main_audio_resynth_idxs:
        raise ValueError("main_audio_cache_revision requires main_audio_resynth_idxs")
    episode = _episode_id(payload)
    source_root = _configured_reel_source_root(payload.get("source_root"))
    actual_implementation_hash = _reel_native_implementation_hash(
        source_root, stage)
    theme = str(payload.get("theme") or "").strip()
    approved_script = None
    approved_authority_type = None
    approved_synthesis_mode = None
    if stage == "script_approved":
        if "approved_script" not in payload:
            raise ValueError("script_approved requires approved_script")
        approved_script = _resolve_reel_input_ref(
            _reel_ref_payload(payload["approved_script"], "approved_script"),
            "approved_script",
        )
    elif "approved_script" in payload:
        raise ValueError("approved_script is only valid for script_approved")
    runner = _trusted_existing_path(
        "CASTLE_REEL_NATIVE_STAGE_SCRIPT",
        WEBUI_ROOT / "orchestrator/reel_native_stage.py",
        "reel_native_stage_script",
    )
    result_path = work_dir / "tmp" / f"native-stage-{stage}.json"
    result_path.unlink(missing_ok=True)
    restored = []
    with _reel_workspace_lock(source_root), ExitStack() as authority_locks:
        expected_runtime_groups = set(REEL_NATIVE_RUNTIME_GROUPS.get(stage, ()))
        runtime_groups = {}
        supplied_runtime_groups = {
            group for group in REEL_RUNTIME_GROUPS
            if f"runtime_{group}_lock" in payload
        }
        if supplied_runtime_groups != expected_runtime_groups:
            raise ValueError(
                f"native Reel stage {stage} runtime groups differ: "
                f"expected={sorted(expected_runtime_groups)} supplied={sorted(supplied_runtime_groups)}")
        for group in sorted(expected_runtime_groups):
            _, runtime_groups[group] = _require_reel_runtime_group_ref(
                payload[f"runtime_{group}_lock"], group, source_root,
                verify_source=True,
            )
        input_fields = sorted(
            key for key in payload if str(key).startswith("input_"))
        # Any immutable input bundle may contain the canonical host authority
        # pointer.  Treat every restore as a writer operation, irrespective of
        # which native stage consumes the bundle.  The clip listener also needs
        # the lock when no restore is supplied because its child reads live host
        # clips without acquiring the authority lock itself.
        if input_fields or stage == "audio_listener_clips":
            authority_locks.enter_context(_reel_host_audio_authority_lock(
                source_root, episode, exclusive=True))
        for field in input_fields:
            restored.extend(_extract_reel_snapshot_bundle(
                payload[field], source_root, field,
                expected_episode=episode,
            ))
        if approved_script is not None:
            canonical_approved = (
                source_root / f"turnlist_reel5_{episode}.json")
            _atomic_copy_hash_pinned(
                approved_script["path"],
                canonical_approved,
                approved_script["content_hash"],
            )
            try:
                approved_payload = json.loads(
                    canonical_approved.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                raise ValueError(
                    "approved_script is not a readable JSON authority") from error
            approved_is_editorial = (
                isinstance(approved_payload, dict)
                and isinstance(
                    approved_payload.get("_editorial_approval"), dict)
            )
            approved_authority_type = (
                "editorial_approval"
                if approved_is_editorial
                else "generated_script_receipt"
            )
            approved_synthesis_mode = (
                "frozen_exact_text" if approved_is_editorial else "canonical"
            )
        if stage != "audio_listener_clips":
            # The final listener consumes the assembled MP4 only, and other
            # native stages either do not read host audio or coordinate their
            # own host writer.  Their lock therefore covers restore only.
            authority_locks.close()
        if stage == "audio_listener_final":
            if "final_input" not in payload:
                raise ValueError("audio_listener_final requires final_input")
            final = _resolve_reel_input_ref(
                _reel_ref_payload(payload["final_input"], "final_input"), "final_input")
            canonical_final = source_root / f"reel{episode}" / f"reel5_{episode}_final.mp4"
            _atomic_copy(final["path"], canonical_final)
        command = [
            sys.executable,
            str(runner),
            "--stage", stage,
            "--source-root", str(source_root),
            "--episode", episode,
            "--result-manifest", str(result_path),
        ]
        if theme:
            command.extend(["--theme", theme])
        if main_audio_resynth_idxs:
            command.extend([
                "--main-audio-resynth-idxs",
                ",".join(str(index) for index in main_audio_resynth_idxs),
                "--main-audio-cache-revision",
                main_audio_cache_revision,
            ])
        child_env = _clean_child_env()
        runtime_cache = (work_dir / "runtime-cache").resolve()
        numba_cache = runtime_cache / "numba"
        matplotlib_cache = runtime_cache / "matplotlib"
        temp_cache = (Path.home() / ".cache/web-comfyui/render-temp" / work_dir.name).resolve()
        numba_cache.mkdir(parents=True, exist_ok=True)
        matplotlib_cache.mkdir(parents=True, exist_ok=True)
        temp_cache.mkdir(parents=True, exist_ok=True)
        child_env["NUMBA_CACHE_DIR"] = str(numba_cache)
        child_env["MPLCONFIGDIR"] = str(matplotlib_cache)
        child_env["TMPDIR"] = str(temp_cache)
        child_env["TEMP"] = str(temp_cache)
        child_env["TMP"] = str(temp_cache)
        if "host_voice" in expected_runtime_groups:
            child_env["HF_HUB_OFFLINE"] = "1"
            child_env["TRANSFORMERS_OFFLINE"] = "1"
        if "asr_models" in expected_runtime_groups:
            child_env["XDG_CACHE_HOME"] = str(
                _reel_runtime_source_roots(source_root)["whisper_cache"].parent)
        if expected_lane == "gpu":
            evidence = _run_reel_gpu_child(
                gpu_queue_cmd(command, payload),
                source_root,
                cancel_file,
                env=child_env,
                error_label=f"native Reel stage {stage}",
            )
            _record_task_event(payload, "task.gpu_queue_observed", "Observed native Reel GPU queue marker", {
                "episode": episode,
                "stage": stage,
                "gpu_queue_marker_count": evidence["gpu_queue_marker_count"],
            })
        else:
            _run_cpu_child(
                command,
                source_root,
                cancel_file,
                env=child_env,
                error_label=f"native Reel stage {stage}",
            )
        rows = _native_stage_result(result_path, source_root, stage=stage, episode=episode)
        expected_bundle_identities = None
        if stage == "scene":
            locked_scene = _require_native_scene_row(
                rows, runtime_groups["scene_assets"], theme)
            expected_bundle_identities = {
                locked_scene["runtime_path"]: {
                    "size": locked_scene["bytes"],
                    "sha256": locked_scene["sha256"],
                },
            }
        bundle_path = work_dir / "tmp" / f"{stage}-{episode}.castle-reel.tar"
        _write_reel_snapshot_bundle(
            source_root,
            [row["path"] for row in rows],
            bundle_path,
            episode=episode,
            stage=stage,
            theme=theme or None,
            expected_identities=expected_bundle_identities,
        )
        snapshots = [
            _snapshot_task_output(
                row["source"], work_dir, index, row["source"].name,
                expected_size=row["size"], expected_sha256=row["sha256"],
            )
            for index, row in enumerate(rows)
        ]
    outputs = []
    for row, snapshot in zip(rows, snapshots):
        suffix = snapshot.suffix.lower()
        artifact_type = (
            "json" if suffix == ".json" else
            "audio" if suffix in {".wav", ".mp3", ".m4a", ".flac", ".ogg"} else
            "image" if suffix in {".png", ".jpg", ".jpeg", ".webp"} else
            "file"
        )
        review_draft = stage == "script" and row["name"] in {
            "script_needs_review", "script_review_verification",
        }
        outputs.append({
            "path": str(snapshot),
            "name": row["source"].name,
            "artifact_type": artifact_type,
            "output_name": row["name"],
            "metadata": {
                "workflow": "castle.reel_native_stage",
                "stage": stage,
                "role": row["name"],
                "episode": episode,
                "implementation_hash": actual_implementation_hash,
                "relative_path": row["path"],
                "stage_output_sha256": row["sha256"],
                "restored_input_count": len(set(restored)),
                **({
                    "authority_type": approved_authority_type,
                    "synthesis_mode": approved_synthesis_mode,
                    "approved_script_sha256": approved_script["content_hash"],
                    "release_eligible": False,
                } if approved_script is not None else {}),
                **({
                    "authority_type": "needs_review_draft",
                    "review_required": True,
                    "release_eligible": False,
                } if review_draft else {}),
            },
            "tags": ["project:ai-sister", "workflow:reel5", f"episode:{episode}", f"stage:{stage}"],
        })
    result_snapshot = _snapshot_task_output(result_path, work_dir, len(outputs), result_path.name)
    outputs.extend([
        {
            "path": str(result_snapshot),
            "name": result_snapshot.name,
            "artifact_type": "json",
            "output_name": "stage_result",
            "metadata": {
                "workflow": "castle.reel_native_stage",
                "stage": stage,
                "role": "stage_result",
                "episode": episode,
                "implementation_hash": actual_implementation_hash,
            },
        },
        {
            "path": str(bundle_path),
            "name": bundle_path.name,
            "artifact_type": "archive",
            "output_name": "stage_bundle",
            "metadata": {
                "workflow": "castle.reel_native_stage",
                "stage": stage,
                "role": "stage_bundle",
                "episode": episode,
                "implementation_hash": actual_implementation_hash,
                "bundle_schema_version": REEL_NATIVE_BUNDLE_SCHEMA_VERSION,
                **({
                    "authority_type": approved_authority_type,
                    "synthesis_mode": approved_synthesis_mode,
                    "approved_script_sha256": approved_script["content_hash"],
                    "release_eligible": False,
                } if approved_script is not None else {}),
                **({
                    "authority_type": "needs_review_draft",
                    "review_required": True,
                    "release_eligible": False,
                } if stage == "script" and any(
                    row["name"] == "script_needs_review" for row in rows
                ) else {}),
                **({"theme": theme} if theme else {}),
            },
            "tags": ["project:ai-sister", "workflow:reel5", f"episode:{episode}", f"stage:{stage}", "role:stage_bundle"],
        },
    ])
    return outputs


def _render_input_manifest_entry(
    source_root: Path,
    role: str,
    relative: str,
    *,
    speaker: str | None = None,
) -> dict:
    relative = _safe_bundle_relative(relative, "render input path")
    path = (source_root / relative).resolve()
    try:
        path.relative_to(source_root.resolve())
    except ValueError as error:
        raise ValueError("render input path escapes source root") from error
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"render input is missing or not regular: {Path(relative).name}")
    entry = {
        "role": str(role),
        "path": relative,
        "sha256": _file_sha256(path),
        "size": path.stat().st_size,
    }
    if speaker is not None:
        entry["speaker"] = str(speaker)
    return entry


def _host_audio_authority_schema(source_root: Path) -> str:
    """Ask the writer what schema name it writes.

    A literal copy here went stale the moment ``host_audio_authority.py``
    moved to the caption/spoken/tts split, and because nothing reached
    ``prepare_render`` between 07-13 and 08-02 nobody found out. The producer
    owns the name; keep one copy of the answer.
    """
    path = (source_root / "host_audio_authority.py").resolve()
    try:
        path.relative_to(source_root.resolve())
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, ValueError) as error:
        raise ValueError("host audio authority schema name is unavailable") from error
    values = [
        node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "HOST_AUDIO_SCHEMA"
            for target in node.targets
        )
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        and node.value.value
    ]
    if len(values) != 1:
        raise ValueError("host audio authority schema name is unavailable")
    return values[0]


def _host_audio_authority_render_inputs(
    source_root: Path,
    episode: str,
    manifest_path: Path,
) -> tuple[str, list[dict]]:
    """Project immutable operator host authority into the render input contract.

    ``clone_host.py`` deliberately publishes no mutable ``00_host.wav`` /
    ``01_host.wav`` aliases.  The authority manifest binds the two semantic
    host slots to one immutable generation, so render preparation must retain
    that binding instead of inventing compatibility files.
    """
    if not re.fullmatch(r"[0-9]+", episode):
        raise ValueError("host audio authority episode must contain digits only")
    source_root = source_root.resolve()
    audio_root = source_root / f"audio_host_{episode}"
    expected_manifest = audio_root / "host_audio_manifest.json"
    if manifest_path.absolute() != expected_manifest.absolute():
        raise ValueError("host audio authority manifest path/DID mismatch")

    def require_plain_directory(path: Path, label: str) -> None:
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError as error:
            raise ValueError(f"host audio authority {label} is missing") from error
        if path.is_symlink() or not stat.S_ISDIR(mode):
            raise ValueError(
                f"host audio authority {label} must be a non-symlink directory")

    require_plain_directory(audio_root, "audio root")
    try:
        manifest_mode = expected_manifest.lstat().st_mode
    except FileNotFoundError as error:
        raise ValueError("host audio authority manifest is missing") from error
    if expected_manifest.is_symlink() or not stat.S_ISREG(manifest_mode):
        raise ValueError("host audio authority manifest must be a regular non-symlink")
    try:
        authority = json.loads(expected_manifest.read_text(encoding="utf-8"))
    except Exception as error:
        raise ValueError("host audio authority manifest is not valid JSON") from error
    if not isinstance(authority, dict):
        raise ValueError("host audio authority manifest must be a JSON object")
    if authority.get("schema") != _host_audio_authority_schema(source_root):
        raise ValueError("host audio authority schema mismatch")
    if str(authority.get("debate_id")) != episode:
        raise ValueError("host audio authority debate ID mismatch")

    commit_id = str(authority.get("commit_id") or "")
    if not re.fullmatch(r"[a-z0-9-]+", commit_id):
        raise ValueError("host audio authority commit ID is malformed")
    generation_relative = (
        f"audio_host_{episode}/.generations/{commit_id}"
    )
    if authority.get("generation") != generation_relative:
        raise ValueError("host audio authority generation identity/path mismatch")
    generations_root = audio_root / ".generations"
    generation = generations_root / commit_id
    require_plain_directory(generations_root, "generations root")
    require_plain_directory(generation, "generation")
    try:
        generation.resolve().relative_to(audio_root.resolve())
    except ValueError as error:
        raise ValueError("host audio authority generation escapes audio root") from error

    artifacts = authority.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != 2:
        raise ValueError("host audio authority must contain exactly idx 0 and 1")
    by_idx: dict[int, dict] = {}
    seen_audio: set[str] = set()
    for position, row in enumerate(artifacts):
        if not isinstance(row, dict):
            raise ValueError(
                f"host audio authority artifact {position} must be an object")
        idx = row.get("idx")
        if type(idx) is not int or idx not in (0, 1):
            raise ValueError("host audio authority artifact idx must be exactly 0 or 1")
        if idx in by_idx:
            raise ValueError(f"duplicate host audio authority artifact idx {idx}")
        if row.get("speaker") != "host":
            raise ValueError(f"host audio authority speaker mismatch at idx {idx}")

        basename = f"{idx:02d}_host.wav"
        relative = f"{generation_relative}/{basename}"
        if row.get("audio") != relative:
            raise ValueError(f"host audio authority path mismatch at idx {idx}")
        if relative in seen_audio:
            raise ValueError("duplicate host audio authority artifact path")
        seen_audio.add(relative)

        declared_size = row.get("size")
        declared_sha = str(row.get("sha256") or "")
        if type(declared_size) is not int or declared_size <= 0:
            raise ValueError(f"host audio authority size is invalid at idx {idx}")
        if not re.fullmatch(r"[0-9a-f]{64}", declared_sha):
            raise ValueError(f"host audio authority SHA-256 is invalid at idx {idx}")
        audio_path = generation / basename
        try:
            audio_mode = audio_path.lstat().st_mode
        except FileNotFoundError as error:
            raise ValueError(
                f"host audio authority file is missing at idx {idx}") from error
        if audio_path.is_symlink() or not stat.S_ISREG(audio_mode):
            raise ValueError(
                f"host audio authority file must be regular and non-symlink at idx {idx}")
        if (
            audio_path.stat().st_size != declared_size
            or _file_sha256(audio_path) != declared_sha
        ):
            raise ValueError(
                f"host audio authority file hash/size mismatch at idx {idx}")
        by_idx[idx] = row

    if set(by_idx) != {0, 1}:
        raise ValueError("host audio authority must contain exactly idx 0 and 1")
    entries = []
    for idx, role in ((0, "intro"), (1, "outro")):
        row = by_idx[idx]
        entries.append({
            "role": role,
            "path": str(row["audio"]),
            "sha256": str(row["sha256"]),
            "size": int(row["size"]),
            "speaker": "host",
            "idx": idx,
        })
    return generation_relative, entries


def _require_host_audio_render_authority_binding(
    source_root: Path,
    episode: str,
    render_manifest: dict,
) -> None:
    """Require the live/restored pointer to equal the frozen render projection."""
    authority_path = (
        source_root / f"audio_host_{episode}" / "host_audio_manifest.json"
    )
    generation, entries = _host_audio_authority_render_inputs(
        source_root, episode, authority_path)
    if render_manifest.get("dir") != generation:
        raise ValueError(
            "host audio render manifest generation differs from authority")
    if render_manifest.get("entries") != entries:
        raise ValueError(
            "host audio render manifest entries differ from authority")


def _native_reel_turns(source_root: Path, episode: str) -> list[dict]:
    path = source_root / f"turnlist_reel5_{episode}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        raise ValueError("turnlist_reel is not valid JSON") from error
    turns = data.get("turns") if isinstance(data, dict) else None
    if not isinstance(turns, list) or not turns:
        raise ValueError("turnlist_reel turns must be non-empty")
    clean = []
    for index, row in enumerate(turns):
        if not isinstance(row, dict) or not isinstance(row.get("idx"), int):
            raise ValueError(f"turnlist_reel row {index} has invalid idx")
        clean.append(row)
    return sorted(clean, key=lambda row: row["idx"])


def _native_reel_cast(source_root: Path, episode: str) -> list[str]:
    cast = []
    for row in _native_reel_turns(source_root, episode):
        if str(row.get("kind") or "line") != "line":
            continue
        speaker = str(row.get("speaker") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", speaker):
            raise ValueError("turnlist_reel contains an unsafe speaker")
        if speaker not in cast:
            cast.append(speaker)
    if not cast:
        raise ValueError("turnlist_reel has no sister cast")
    return cast


def _native_sister_rig_directories(
    source_root: Path, episode: str, theme: str,
) -> list[tuple[str, Path]]:
    selected_rows = []
    parts_root = source_root / "tachie" / "parts"
    for speaker in _native_reel_cast(source_root, episode):
        selected = None
        for dirname in (
            f"{speaker}__{theme}__v2_parts",
            f"{speaker}_{theme}_v2_parts",
            f"{speaker}_parts",
        ):
            candidate = parts_root / dirname
            if (candidate / "parts.json").is_file():
                selected = candidate
                break
        if selected is None:
            raise ValueError(f"approved rig is missing for {speaker}")
        selected_rows.append((speaker, selected))
    return selected_rows


def _native_sister_rig_entries(source_root: Path, episode: str, theme: str) -> list[dict]:
    entries = []
    seen = set()
    for speaker, selected in _native_sister_rig_directories(
        source_root, episode, theme,
    ):
        paths = [selected / "parts.json"]
        if (selected / "source.json").is_file():
            paths.append(selected / "source.json")
        paths.extend(sorted(selected.glob("*.png")))
        for path in paths:
            relative = path.resolve().relative_to(source_root.resolve()).as_posix()
            if relative in seen:
                continue
            seen.add(relative)
            entries.append(_render_input_manifest_entry(
                source_root, relative, relative, speaker=speaker))
    return entries


def _qc_rig_source_records(
    source_root: Path, episode: str, theme: str,
) -> list[dict]:
    voice_lab = source_root.parent.resolve()
    records = []
    seen = set()

    def add_source(path: Path, speaker: str, kind: str, expected_hash: str = "") -> None:
        unresolved = path.expanduser().absolute()
        if unresolved.is_symlink():
            raise ValueError(f"QC rig source is a symlink for {speaker}")
        resolved = unresolved.resolve()
        try:
            relative = resolved.relative_to(voice_lab).as_posix()
        except ValueError as error:
            raise ValueError("QC rig source escapes voice-lab") from error
        if not resolved.is_file():
            raise ValueError(f"QC rig source is missing for {speaker}")
        digest = _file_sha256(resolved)
        if expected_hash and (
            not re.fullmatch(r"[0-9a-f]{64}", expected_hash)
            or expected_hash != digest
        ):
            raise ValueError(f"QC rig source_sha256 differs for {speaker}")
        key = (speaker, relative)
        if key in seen:
            return
        seen.add(key)
        records.append({
            "speaker": speaker,
            "kind": kind,
            "path": relative,
            "size": resolved.stat().st_size,
            "sha256": digest,
        })

    for speaker, parts_dir in _native_sister_rig_directories(
        source_root, episode, theme,
    ):
        source_meta_path = parts_dir / "source.json"
        source_meta = None
        if source_meta_path.is_file() and not source_meta_path.is_symlink():
            try:
                source_meta = json.loads(source_meta_path.read_text(encoding="utf-8"))
            except Exception as error:
                raise ValueError(f"QC rig source metadata is invalid for {speaker}") from error
            if not isinstance(source_meta, dict) or not str(source_meta.get("source") or "").strip():
                raise ValueError(f"QC rig source metadata is incomplete for {speaker}")
            source_value = Path(str(source_meta["source"])).expanduser()
            if not source_value.is_absolute():
                source_value = parts_dir / source_value
            add_source(
                source_value,
                speaker,
                "source_json",
                str(source_meta.get("source_sha256") or ""),
            )

        try:
            parts = json.loads((parts_dir / "parts.json").read_text(encoding="utf-8"))
        except Exception as error:
            raise ValueError(f"QC rig parts manifest is invalid for {speaker}") from error
        psd_src = str(parts.get("src") or "") if isinstance(parts, dict) else ""
        canvas_source = None
        if psd_src:
            canvas_source = Path(os.path.splitext(psd_src)[0]) / "src_img.png"
            if canvas_source.is_file():
                add_source(canvas_source, speaker, "parts_canvas")
        if source_meta is None and (canvas_source is None or not canvas_source.is_file()):
            add_source(
                source_root / "tachie/outfits_v2_norm" / f"doll_{speaker}__{theme}.png",
                speaker,
                "normalized_fallback",
            )
    if not records:
        raise ValueError("QC rig source closure is empty")
    return sorted(records, key=lambda row: (row["speaker"], row["path"]))


def _native_host_rig_entries(source_root: Path, theme: str) -> list[dict]:
    root = source_root / "tachie" / "parts" / f"host_{theme}_parts"
    if not (root / "parts.json").is_file():
        raise ValueError("approved operator host rig is missing")
    paths = [root / "parts.json"]
    if (root / "source.json").is_file():
        paths.append(root / "source.json")
    paths.extend(sorted(root.glob("*.png")))
    return [
        _render_input_manifest_entry(
            source_root,
            path.resolve().relative_to(source_root.resolve()).as_posix(),
            path.resolve().relative_to(source_root.resolve()).as_posix(),
            speaker="host",
        )
        for path in paths
    ]


def _write_render_input_manifest(path: Path, *, directory: str, entries: list[dict]) -> None:
    if not entries:
        raise ValueError("render input manifest requires entries")
    value = {
        "schema_version": REEL_RENDER_INPUT_MANIFEST_SCHEMA_VERSION,
        "dir": directory,
        "entries": entries,
    }
    _write_text(path, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def _verify_prepared_audio_authorities(
    source_root: Path,
    episode: str,
    cancel_file: Path,
) -> list[str]:
    """Verify champion and complete audio-ready proofs without producing bytes."""
    python = _trusted_existing_path(
        "CASTLE_REEL_NATIVE_PYTHON",
        Path.home() / ".local/share/web-comfyui/integrations/voice/.venv/bin/python",
        "canonical Reel Python",
    )
    champion_script = _trusted_existing_path(
        "CASTLE_REEL_RENDER_CHAMPION_AUTHORITY_SCRIPT",
        source_root / "champion_authority.py",
        "champion authority verifier",
    )
    audio_ready_script = _trusted_existing_path(
        "CASTLE_REEL_RENDER_AUDIO_READY_AUTHORITY_SCRIPT",
        source_root / "audio_ready_authority.py",
        "audio-ready authority verifier",
    )
    env = _clean_child_env()
    env["HF_HUB_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    _run_cpu_child(
        [
            str(python), str(champion_script), "check",
            "--manifest", f"champion_{episode}.json",
            "--debate-id", episode,
            "--host-turnlist", f"turnlist_host_{episode}.json",
            "--reel-turnlist", f"turnlist_reel5_{episode}.json",
            "--react-beats", f"react_beats_peer_{episode}.json",
            "--audio-dir", f"audio_champion_{episode}",
            "--champion-lines", "champion_lines.json",
            "--root", str(source_root),
            "--seed", episode,
        ],
        source_root,
        cancel_file,
        env=env,
        error_label="prepared champion authority",
    )
    _run_cpu_child(
        [
            str(python), str(audio_ready_script), "check", episode,
            "--authority", f"audio_ready_{episode}.json",
            "--root", str(source_root),
        ],
        source_root,
        cancel_file,
        env=env,
        error_label="prepared audio-ready authority",
    )
    return _reel_champion_authority_paths(
        source_root, episode, source_root / f"champion_{episode}.json")


def run_castle_reel_prepare_render(payload: dict, work_dir: Path, cancel_file: Path) -> list[dict]:
    allowed = {
        "episode", "source_root", "output_root", "theme", "runtime_receipt",
        *(f"runtime_{group}_lock" for group in REEL_RUNTIME_GROUPS),
    }
    unknown = sorted(
        key for key in payload
        if not str(key).startswith("_")
        and key not in allowed
        and not str(key).startswith("input_")
    )
    if unknown:
        raise ValueError(f"castle.reel_prepare_render unknown fields: {', '.join(unknown)}")
    episode = _episode_id(payload)
    source_root = _configured_reel_source_root(payload.get("source_root"))
    output_root = _safe_output_dir(payload.get("output_root"), "output_root")
    theme = str(payload.get("theme") or "").strip()
    if theme not in REEL_CANONICAL_THEMES:
        raise ValueError("theme must be a canonical Reel category")
    if "runtime_receipt" not in payload:
        raise ValueError("runtime_receipt is required")
    runtime = _resolve_reel_input_ref(
        _reel_ref_payload(payload["runtime_receipt"], "runtime_receipt"),
        "runtime_receipt",
    )
    if runtime["kind"] != "artifact":
        raise ValueError("runtime_receipt must be an adopted Castle artifact")
    runtime_metadata = runtime.get("metadata", {})
    if (
        runtime_metadata.get("workflow") != "castle.reel_runtime_verify"
        or runtime_metadata.get("role") != "runtime_receipt"
    ):
        raise ValueError("runtime_receipt has the wrong Castle producer")
    try:
        runtime_receipt = json.loads(runtime["path"].read_text(encoding="utf-8"))
    except Exception as error:
        raise ValueError("runtime_receipt is not valid JSON") from error
    locked_runtime = _configured_reel_runtime_lock()
    expected_runtime = {
        "bundle_id": locked_runtime["bundle_id"],
        "content_set_sha256": locked_runtime["content_set_sha256"],
        "entry_count": locked_runtime["entry_count"],
        "total_bytes": locked_runtime["total_bytes"],
        "lock_sha256": locked_runtime["lock_sha256"],
    }
    if (
        not isinstance(runtime_receipt, dict)
        or runtime_receipt.get("schema_version") != "castle-reel-runtime-source-receipt-v1"
        or runtime_receipt.get("source_ok") is not True
        or runtime_receipt.get("mode") != "source-deep"
        or any(runtime_receipt.get(key) != value for key, value in expected_runtime.items())
        or any(runtime_metadata.get(key) != value for key, value in expected_runtime.items())
        or runtime_metadata.get("mode") != "source-deep"
        or not re.fullmatch(r"[0-9a-f]{64}", str(runtime_receipt.get("manager_sha256") or ""))
        or runtime_metadata.get("manager_sha256") != runtime_receipt.get("manager_sha256")
    ):
        raise ValueError("runtime_receipt is not healthy")

    restored = []
    with _reel_workspace_lock(source_root), _reel_host_audio_authority_lock(
        source_root, episode, exclusive=True,
    ):
        runtime_groups = {}
        for group in REEL_RUNTIME_GROUPS:
            field = f"runtime_{group}_lock"
            if field not in payload:
                raise ValueError(f"{field} is required")
            _, runtime_groups[group] = _require_reel_runtime_group_ref(
                payload[field], group, source_root,
                verify_source=group != "scene_assets")
        for field in sorted(key for key in payload if str(key).startswith("input_")):
            restored.extend(_extract_reel_snapshot_bundle(
                payload[field], source_root, field,
                expected_episode=episode,
                expected_stage="scene" if field == "input_02_scene" else None,
                expected_theme=theme if field == "input_02_scene" else None,
            ))
        # Runtime groups were verified before the immutable stage bundles were
        # restored.  A bundle can itself contain tachie/scenes/<theme>.png, so
        # bind the post-restore bytes back to the scene_assets lock before any
        # render input snapshot is created.
        locked_scene = _require_locked_reel_scene_source(
            source_root, runtime_groups["scene_assets"], theme)

        required = {
            "turnlist_reel": source_root / f"turnlist_reel5_{episode}.json",
            "turnlist_host": source_root / f"turnlist_host_{episode}.json",
            "peer_beats": source_root / f"react_beats_peer_{episode}.json",
            "host_beats": source_root / f"react_beats_host_{episode}.json",
            "champion_lines": source_root / "champion_lines.json",
            "spoken": source_root / f"audio_reel5_{episode}" / "spoken.json",
            "timing": source_root / f"audio_reel5_{episode}" / f"timing_{episode}.json",
            "host_audio_authority": (
                source_root / f"audio_host_{episode}" / "host_audio_manifest.json"
            ),
            "champion_authority": source_root / f"champion_{episode}.json",
            "audio_listener_clips": (
                source_root / f"audio_listener_clips_{episode}.json"
            ),
            "audio_ready": source_root / f"audio_ready_{episode}.json",
            "scene": source_root / "tachie" / "scenes" / f"{theme}.png",
        }
        for role, path in required.items():
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"prepare_render missing {role}")
        champion_paths = _verify_prepared_audio_authorities(
            source_root, episode, cancel_file)

        audio_entries = [
            _render_input_manifest_entry(
                source_root, "spoken", f"audio_reel5_{episode}/spoken.json"),
            _render_input_manifest_entry(
                source_root, "timing", f"audio_reel5_{episode}/timing_{episode}.json"),
        ]
        host_audio_directory, host_audio_entries = (
            _host_audio_authority_render_inputs(
                source_root, episode, required["host_audio_authority"])
        )
        sister_rig_entries = _native_sister_rig_entries(source_root, episode, theme)
        host_rig_entries = _native_host_rig_entries(source_root, theme)
        qc_rig_records = _qc_rig_source_records(source_root, episode, theme)
        sfx_entries = [
            _render_input_manifest_entry(source_root, role, relative)
            for role, relative in REEL_SFX_DEFAULTS.items()
        ]
        runtime_sfx_paths = []
        host_sfx_entries = []
        host_sfx_index = 0
        for group in ("sfx_outputs", "sfx_sources"):
            for entry in runtime_groups[group]["entries"]:
                if entry["source"]["root"] != "video_mvp":
                    continue
                relative = entry["source"]["path"]
                runtime_sfx_paths.append(relative)
                role = "pop" if entry["runtime_path"] == "sfx_pop.wav" else f"runtime_{host_sfx_index:02d}"
                host_sfx_index += 1
                host_sfx_entries.append(
                    _render_input_manifest_entry(source_root, role, relative))
        if not any(entry["role"] == "pop" for entry in host_sfx_entries):
            raise ValueError("locked runtime is missing host pop SFX")
        manifest_paths = {
            "audio_manifest": output_root / "audio_manifest.json",
            "host_audio_manifest": output_root / "host_audio_manifest.json",
            "rig_manifest": output_root / "rig_manifest.json",
            "host_rig_manifest": output_root / "host_rig_manifest.json",
            "sfx_manifest": output_root / "sfx_manifest.json",
            "host_sfx_manifest": output_root / "host_sfx_manifest.json",
            "qc_rig_sources_manifest": output_root / "qc_rig_sources_manifest.json",
        }
        _write_render_input_manifest(
            manifest_paths["audio_manifest"],
            directory=f"audio_reel5_{episode}",
            entries=audio_entries,
        )
        _write_render_input_manifest(
            manifest_paths["host_audio_manifest"],
            directory=host_audio_directory,
            entries=host_audio_entries,
        )
        _write_render_input_manifest(
            manifest_paths["rig_manifest"], directory="", entries=sister_rig_entries)
        _write_render_input_manifest(
            manifest_paths["host_rig_manifest"], directory="", entries=host_rig_entries)
        _write_render_input_manifest(
            manifest_paths["sfx_manifest"], directory="", entries=sfx_entries)
        _write_render_input_manifest(
            manifest_paths["host_sfx_manifest"], directory="", entries=host_sfx_entries)
        _write_text(
            manifest_paths["qc_rig_sources_manifest"],
            json.dumps({
                "schema_version": "castle-reel-qc-rig-sources-v1",
                "episode": episode,
                "theme": theme,
                "voice_lab_root": "voice-lab",
                "sources": qc_rig_records,
            }, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        )

        music_scripts = _resolve_reel_script_paths()
        music_env = _clean_child_env()
        music_env["MUSIC_AUTO_GEN"] = "0"
        music_output = _run_cpu_child(
            [
                str(music_scripts["python"]), str(music_scripts["policy"]),
                theme, str(len(_native_reel_cast(source_root, episode))),
            ],
            source_root,
            cancel_file,
            env=music_env,
            error_label="locked Reel music policy",
        )
        music_value = next(
            (line.strip() for line in reversed(music_output.splitlines()) if line.strip()), "")
        music_path = Path(music_value).expanduser()
        if not music_path.is_absolute():
            music_path = source_root / music_path
        music_path = music_path.resolve()
        try:
            music_relative = music_path.relative_to(source_root).as_posix()
        except ValueError as error:
            raise ValueError("music policy selected a bed outside the Reel source root") from error
        locked_music = next((
            entry for entry in runtime_groups["music"]["entries"]
            if entry["source"]["root"] == "video_mvp"
            and entry["source"]["path"] == music_relative
        ), None)
        if (
            locked_music is None
            or music_path.is_symlink()
            or not music_path.is_file()
            or music_path.stat().st_size != locked_music["bytes"]
            or _file_sha256(music_path) != locked_music["sha256"]
        ):
            raise ValueError("music policy selected a bed outside the locked runtime")

        workspace_paths = set(restored)
        workspace_paths.update(
            path.resolve().relative_to(source_root.resolve()).as_posix()
            for path in required.values()
        )
        workspace_paths.update(runtime_sfx_paths)
        workspace_paths.update(champion_paths)
        for entries in (audio_entries, host_audio_entries, sister_rig_entries, host_rig_entries, sfx_entries):
            workspace_paths.update(entry["path"] for entry in entries)
        workspace_paths.update(
            path.resolve().relative_to(source_root.resolve()).as_posix()
            for path in manifest_paths.values()
        )
        # These are runtime-read policy/art assets, not implementation-only
        # decorations.  Snapshot their exact bytes so a reused prepare/render
        # subgraph cannot silently consume newer files from the shared tree.
        _reel_policy_data_records(source_root)
        _reel_pose_asset_records(source_root)
        workspace_paths.add("forum_title_overrides.json")
        workspace_paths.update(
            path.relative_to(source_root).as_posix()
            for path in sorted(
                (source_root / "tachie/poses/react").glob("*.png"),
                key=lambda item: item.name,
            )
        )
        workspace_bundle = work_dir / "tmp" / f"render-workspace-{episode}.castle-reel.tar"
        _write_reel_snapshot_bundle(
            source_root,
            workspace_paths,
            workspace_bundle,
            episode=episode,
            stage="prepare_render",
            expected_identities={
                locked_scene["runtime_path"]: {
                    "size": locked_scene["bytes"],
                    "sha256": locked_scene["sha256"],
                },
            },
        )
        qc_rig_bundle = work_dir / "tmp" / f"qc-rig-sources-{episode}.castle-reel.tar"
        qc_rig_bundle_manifest = _write_reel_snapshot_bundle(
            source_root.parent,
            [row["path"] for row in qc_rig_records],
            qc_rig_bundle,
            episode=episode,
            stage="prepare_render_qc_rigs",
        )

        direct = {
            "turnlist_reel": required["turnlist_reel"],
            "turnlist_host": required["turnlist_host"],
            "peer_beats": required["peer_beats"],
            "host_beats": required["host_beats"],
            "champion_lines": required["champion_lines"],
            "champion_authority": required["champion_authority"],
            "audio_listener_clips": required["audio_listener_clips"],
            "audio_ready": required["audio_ready"],
            **manifest_paths,
        }
        snapshots = {
            role: _snapshot_task_output(path, work_dir, index, path.name)
            for index, (role, path) in enumerate(direct.items())
        }
        music_snapshot = _snapshot_task_output(
            music_path, work_dir, len(snapshots), music_path.name)
    outputs = []
    for role, snapshot in snapshots.items():
        outputs.append({
            "path": str(snapshot),
            "name": snapshot.name,
            "artifact_type": "json",
            "output_name": role,
            "metadata": {
                "workflow": "castle.reel_prepare_render",
                "stage": "prepare_render",
                "role": role,
                "episode": episode,
                "theme": theme,
                "runtime_content_set_sha256": runtime_receipt.get("content_set_sha256"),
            },
            "tags": ["project:ai-sister", "workflow:reel5", f"episode:{episode}", "stage:prepare_render", f"role:{role}"],
        })
    outputs.append({
        "path": str(workspace_bundle),
        "name": workspace_bundle.name,
        "artifact_type": "archive",
        "output_name": "workspace_bundle",
        "metadata": {
            "workflow": "castle.reel_prepare_render",
            "stage": "prepare_render",
            "role": "workspace_bundle",
            "episode": episode,
            "theme": theme,
            "snapshot_schema_version": REEL_RENDER_SNAPSHOT_SCHEMA_VERSION,
            "restored_input_count": len(set(restored)),
            "runtime_content_set_sha256": runtime_receipt.get("content_set_sha256"),
            "runtime_lock_sha256": runtime_receipt.get("lock_sha256"),
        },
        "tags": ["project:ai-sister", "workflow:reel5", f"episode:{episode}", "stage:prepare_render", "role:workspace_bundle"],
    })
    outputs.append({
        "path": str(music_snapshot),
        "name": music_snapshot.name,
        "artifact_type": "audio",
        "output_name": "music_bed",
        "metadata": {
            "workflow": "castle.reel_prepare_render",
            "stage": "prepare_render",
            "role": "music_bed",
            "episode": episode,
            "theme": theme,
            "runtime_group_content_sha256": runtime_groups["music"]["group_content_sha256"],
            "source_sha256": locked_music["sha256"],
        },
        "tags": [
            "project:ai-sister", "workflow:reel5", f"episode:{episode}",
            "stage:prepare_render", "role:music_bed",
        ],
    })
    outputs.append({
        "path": str(qc_rig_bundle),
        "name": qc_rig_bundle.name,
        "artifact_type": "archive",
        "output_name": "qc_rig_bundle",
        "metadata": {
            "workflow": "castle.reel_prepare_render",
            "stage": "prepare_render",
            "role": "qc_rig_bundle",
            "episode": episode,
            "theme": theme,
            "snapshot_schema_version": REEL_NATIVE_BUNDLE_SCHEMA_VERSION,
            "snapshot_file_count": len(qc_rig_bundle_manifest["files"]),
        },
        "tags": [
            "project:ai-sister", "workflow:reel5", f"episode:{episode}",
            "stage:prepare_render", "role:qc_rig_bundle",
        ],
    })
    return outputs


def _safe_reel_relative(value, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    path = Path(text)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{field} must be relative and must not contain ..")
    return text


def _safe_reel_output_name(value, fallback: str) -> str:
    raw = str(value or fallback).strip()
    raw_path = Path(raw)
    if raw_path.is_absolute() or ".." in raw_path.parts or len(raw_path.parts) != 1:
        raise ValueError("output_name must be a single relative filename and must not contain ..")
    return _safe_output_name(raw, fallback)


def _relative_to_source(path: Path, source_root: Path, field: str) -> str:
    try:
        return str(path.resolve().relative_to(source_root.resolve()))
    except ValueError as error:
        raise ValueError(f"{field} must resolve under the reel source root") from error


def _reel_cast_from_turnlist(path: Path) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        turns = data.get("turns") if isinstance(data, dict) else None
        speakers = []
        for turn in turns or []:
            if isinstance(turn, dict):
                speaker = str(turn.get("speaker") or "").strip()
                if speaker and speaker not in speakers:
                    speakers.append(speaker)
    except Exception as error:
        raise ValueError("turnlist_reel is not valid JSON with turns") from error
    if not speakers:
        raise ValueError("turnlist_reel has no speakers")
    return str(len(speakers))


def _resolve_reel_script_paths() -> dict:
    source_root = _trusted_existing_path(
        "CASTLE_REEL5_SOURCE_ROOT",
        Path.home() / ".local/share/web-comfyui/integrations/voice/video-mvp",
        "reel_source_root",
        file=False,
    )
    assemble = _trusted_existing_path(
        "CASTLE_REEL_ASSEMBLE_SCRIPT",
        source_root / "assemble_reel5.sh",
        "assemble_script",
    )
    xfade = _trusted_existing_path(
        "CASTLE_REEL_XFADE_SCRIPT",
        source_root / "assemble_xfade.py",
        "assemble_xfade_script",
    )
    policy = _trusted_existing_path(
        "CASTLE_REEL_MUSIC_POLICY_SCRIPT",
        source_root / "music_policy.py",
        "music_policy_script",
    )
    python = _env_path("CASTLE_REEL_PYTHON", Path.home() / ".local/share/web-comfyui/integrations/voice/.venv/bin/python")
    if not python.is_file():
        python = Path(sys.executable).resolve()
    return {
        "source_root": source_root,
        "assemble": assemble,
        "xfade": xfade,
        "policy": policy,
        "python": python,
        "assemble_hash": _file_sha256(assemble),
        "xfade_hash": _file_sha256(xfade),
        "policy_hash": _file_sha256(policy),
    }


def _resolve_reel_music_bed(spec: dict, scripts: dict, cancel_file: Path) -> dict:
    source_root = scripts["source_root"]
    override = spec.get("music_bed")
    if isinstance(override, dict):
        resolved = _resolve_reel_input_ref(override, "music_bed")
        path = resolved["path"]
        if resolved["kind"] == "artifact":
            _require_reel_artifact_producer(
                resolved, "music_bed", workflow="castle.reel_prepare_render",
                role="music_bed")
        elif not _path_is_allowed(path, [source_root]):
            raise ValueError("music_bed must resolve under the reel source root")
        runtime_music = spec.get("runtime_music_document")
        if runtime_music is not None and not any(
            entry["sha256"] == resolved["content_hash"]
            and entry["bytes"] == path.stat().st_size
            for entry in runtime_music["entries"]
        ):
            raise ValueError("music_bed artifact is not part of the locked runtime")
        return {
            "source": "override",
            "path": path,
            "basename": path.name,
            "content_hash": resolved["content_hash"],
            "identity": {"kind": resolved["kind"], "content_hash": resolved["content_hash"], "basename": path.name},
        }
    if isinstance(override, str) and override.strip():
        rel = _safe_reel_relative(override, "music_bed")
        path = (source_root / rel).resolve()
        if not path.is_file():
            raise ValueError("music_bed not found")
        return {
            "source": "override",
            "path": path,
            "basename": path.name,
            "content_hash": _file_sha256(path),
            "identity": {"kind": "source_relative", "path": rel, "content_hash": _file_sha256(path)},
        }
    env = _clean_child_env()
    env["MUSIC_AUTO_GEN"] = "0"
    output = _run_cpu_child(
        [str(scripts["python"]), str(scripts["policy"]), spec["theme"], spec["cast"]],
        scripts["source_root"],
        cancel_file,
        env=env,
        error_label="music policy",
    )
    bed_text = ""
    for line in output.splitlines():
        candidate = line.strip()
        if candidate:
            bed_text = candidate
    if not bed_text:
        raise RuntimeError("music policy returned no bed")
    bed_path = Path(bed_text).expanduser()
    if not bed_path.is_absolute():
        bed_path = source_root / bed_path
    bed_path = bed_path.resolve()
    if not _path_is_allowed(bed_path, [source_root]):
        raise ValueError("music policy returned a bed outside the reel source root")
    if not bed_path.is_file():
        raise ValueError("music policy returned a missing bed")
    bed_hash = _file_sha256(bed_path)
    return {
        "source": "policy",
        "path": bed_path,
        "basename": bed_path.name,
        "content_hash": bed_hash,
        "identity": {"kind": "policy", "relative_path": _relative_to_source(bed_path, source_root, "music_bed"), "content_hash": bed_hash},
    }


def _require_reel_assemble_payload(payload: dict, scripts: dict) -> dict:
    required = {
        "episode",
        "theme",
        "output_root",
        "reel_subdir",
        "turnlist_reel",
        "turnlist_host",
        "champion",
        "cover",
        "sisters_formula",
        "host_formula",
        "source_content_hash",
    }
    optional = {
        "cover_hold", "music_bed", "music_vol", "xfade", "output_name",
        "segment_set_manifest", "render_sisters_manifest", "render_host_manifest",
        "render_sisters_bundle", "render_host_bundle",
        "implementation_hash",
        "workspace_bundle", "runtime_music_lock",
    }
    allowed = required | optional
    unknown = sorted(key for key in payload if not str(key).startswith("_") and key not in allowed)
    if unknown:
        raise ValueError(f"castle.reel_assemble unknown fields: {', '.join(unknown)}")
    missing = sorted(key for key in required if key not in payload)
    if missing:
        raise ValueError(f"castle.reel_assemble missing fields: {', '.join(missing)}")
    has_segment_manifest = "segment_set_manifest" in payload
    has_render_sisters_manifest = "render_sisters_manifest" in payload
    has_render_host_manifest = "render_host_manifest" in payload
    has_render_pair = has_render_sisters_manifest and has_render_host_manifest
    if has_render_sisters_manifest != has_render_host_manifest:
        raise ValueError("castle.reel_assemble render_sisters_manifest and render_host_manifest must be supplied together")
    has_sisters_bundle = "render_sisters_bundle" in payload
    has_host_bundle = "render_host_bundle" in payload
    if has_sisters_bundle != has_host_bundle:
        raise ValueError("castle.reel_assemble render segment bundles must be supplied together")
    if has_segment_manifest and (has_render_sisters_manifest or has_render_host_manifest):
        raise ValueError("castle.reel_assemble requires exactly one of segment_set_manifest or both render_sisters_manifest and render_host_manifest")
    if not has_segment_manifest and not has_render_pair:
        raise ValueError("castle.reel_assemble requires exactly one of segment_set_manifest or both render_sisters_manifest and render_host_manifest")
    episode = str(payload.get("episode") or "").strip()
    if not re.fullmatch(r"[0-9]+", episode):
        raise ValueError("episode must contain digits only")
    theme = str(payload.get("theme") or "").strip()
    if not theme:
        raise ValueError("theme is required")
    source_content_hash = str(payload.get("source_content_hash") or "").strip()
    if not source_content_hash:
        raise ValueError("source_content_hash is required")
    reel_subdir = _safe_reel_relative(payload.get("reel_subdir"), "reel_subdir")
    if len(Path(reel_subdir).parts) != 1:
        raise ValueError("reel_subdir must be a single relative directory name")
    refs = {}
    ref_fields = ["turnlist_reel", "turnlist_host", "champion", "cover", "sisters_formula", "host_formula"]
    if has_segment_manifest:
        ref_fields.append("segment_set_manifest")
    else:
        ref_fields.extend(["render_sisters_manifest", "render_host_manifest"])
    if has_sisters_bundle:
        ref_fields.extend(["render_sisters_bundle", "render_host_bundle"])
    if "workspace_bundle" in payload:
        ref_fields.append("workspace_bundle")
    for field in ref_fields:
        refs[field] = _resolve_reel_input_ref(_reel_ref_payload(payload.get(field), field), field)
    source_root = scripts["source_root"]
    turnlist_reel_rel = _relative_to_source(refs["turnlist_reel"]["path"], source_root, "turnlist_reel")
    turnlist_host_rel = _relative_to_source(refs["turnlist_host"]["path"], source_root, "turnlist_host")
    if refs["cover"]["kind"] == "artifact" and refs["cover"].get("metadata", {}).get("workflow") != "castle.reel_cover":
        raise ValueError("cover artifact ref must come from castle.reel_cover")
    _require_reel_artifact_producer(refs["sisters_formula"], "sisters_formula", workflow="ai_sister.render_sisters", role="sisters_formula")
    _require_reel_artifact_producer(refs["host_formula"], "host_formula", workflow="ai_sister.render_host", role="host_formula")
    if refs["champion"]["kind"] == "artifact":
        _require_reel_artifact_producer(
            refs["champion"], "champion",
            workflow="castle.reel_native_stage", role="champion_authority")
        if refs["champion"].get("metadata", {}).get("stage") != "champion_synth":
            raise ValueError("champion must come from native champion_synth")
    if "workspace_bundle" in refs:
        _require_reel_artifact_producer(
            refs["workspace_bundle"], "workspace_bundle",
            workflow="castle.reel_prepare_render", role="workspace_bundle")
    runtime_music_ref = None
    runtime_music_document = None
    if "runtime_music_lock" in payload:
        runtime_music_ref, runtime_music_document = _require_reel_runtime_group_ref(
            payload["runtime_music_lock"], "music", source_root,
            verify_source=False,
        )
        refs["runtime_music_lock"] = runtime_music_ref
    render_manifests = {}
    if not has_segment_manifest:
        _require_reel_artifact_producer(refs["render_sisters_manifest"], "render_sisters_manifest", workflow="ai_sister.render_sisters", role="render_sisters")
        _require_reel_artifact_producer(refs["render_host_manifest"], "render_host_manifest", workflow="ai_sister.render_host", role="render_host")
        render_manifests["render_sisters_manifest"] = _require_render_output_manifest(
            refs["render_sisters_manifest"]["path"],
            "render_sisters_manifest",
            stage="render_sisters",
            episode=episode,
            source_root=source_root,
            reel_subdir=reel_subdir,
            expected_clips=_expected_render_clips_from_turnlist(refs["turnlist_reel"]["path"], "turnlist_reel"),
        )
        render_manifests["render_host_manifest"] = _require_render_output_manifest(
            refs["render_host_manifest"]["path"],
            "render_host_manifest",
            stage="render_host",
            episode=episode,
            source_root=source_root,
            reel_subdir=reel_subdir,
            expected_clips=_expected_render_clips_from_turnlist(refs["turnlist_host"]["path"], "turnlist_host"),
        )
        _require_render_formula_matches_manifest(
            refs["sisters_formula"]["path"],
            "sisters_formula",
            stage="render_sisters",
            manifest=render_manifests["render_sisters_manifest"],
        )
        _require_render_formula_matches_manifest(
            refs["host_formula"]["path"],
            "host_formula",
            stage="render_host",
            manifest=render_manifests["render_host_manifest"],
        )
    cast = _reel_cast_from_turnlist(refs["turnlist_reel"]["path"])
    output_name = _safe_reel_output_name(payload.get("output_name"), f"reel5_{episode}_final.mp4")
    return {
        "episode": episode,
        "theme": theme,
        "output_root": _safe_output_path(payload.get("output_root"), "output_root"),
        "reel_subdir": reel_subdir,
        "turnlist_reel_rel": turnlist_reel_rel,
        "turnlist_host_rel": turnlist_host_rel,
        "refs": refs,
        "assemble_manifest_mode": "segment_set" if has_segment_manifest else "render_manifests",
        "render_manifests": render_manifests,
        "cast": cast,
        "cover_hold": _clamped_float(payload.get("cover_hold"), 2.6, 0.1, 30.0),
        "music_bed": _reel_ref_payload(payload["music_bed"], "music_bed") if isinstance(payload.get("music_bed"), dict) else payload.get("music_bed"),
        "music_vol": _clamped_float(payload.get("music_vol"), 0.16, 0.0, 2.0),
        "xfade": _clamped_float(payload.get("xfade"), 0.4, 0.0, 5.0),
        "output_name": output_name,
        "source_content_hash": source_content_hash,
        "implementation_hash": str(payload.get("implementation_hash") or "").strip(),
        "runtime_music_document": runtime_music_document,
        "task_schema_version": REEL_ASSEMBLE_TASK_SCHEMA_VERSION,
    }


def _require_reel_artifact_producer(ref: dict, field: str, *, workflow: str, role: str) -> None:
    if ref["kind"] != "artifact":
        return
    metadata = ref.get("metadata", {})
    if metadata.get("workflow") != workflow:
        raise ValueError(f"{field} artifact ref must come from {workflow}")
    if metadata.get("role") != role:
        raise ValueError(f"{field} artifact ref must have role {role}")


def _require_reel_snapshot_bundle_artifact(
    value,
    field: str,
    *,
    workflow: str,
    role: str,
    expected_episode: str,
) -> dict:
    if not re.fullmatch(r"[0-9]+", expected_episode):
        raise ValueError(f"{field} expected episode must contain digits only")
    ref = _resolve_reel_input_ref(_reel_ref_payload(value, field), field)
    if ref["kind"] != "artifact":
        raise ValueError(f"{field} must be an adopted Castle artifact")
    _require_reel_artifact_producer(
        ref, field, workflow=workflow, role=role)
    if str(ref.get("metadata", {}).get("episode")) != expected_episode:
        raise ValueError(f"{field} artifact episode mismatch")
    return ref


def _reel_ref_hash_view(ref: dict) -> dict:
    view = {"kind": ref["kind"], "content_hash": ref["content_hash"], "basename": ref.get("basename")}
    if ref["kind"] == "artifact":
        view["artifact_id"] = ref["artifact_id"]
    return {key: value for key, value in view.items() if value}


def _reel_assemble_input_hash(
    spec: dict,
    scripts: dict,
    bed: dict,
    implementation_hash: str,
) -> str:
    refs = spec["refs"]
    basis = {
        "task_schema_version": REEL_ASSEMBLE_TASK_SCHEMA_VERSION,
        "episode": spec["episode"],
        "theme": spec["theme"],
        "cast": spec["cast"],
        "bed": bed["identity"],
        "cover_hold": spec["cover_hold"],
        "music_vol": spec["music_vol"],
        "xfade": spec["xfade"],
        "turnlist_reel": refs["turnlist_reel"]["content_hash"],
        "turnlist_host": refs["turnlist_host"]["content_hash"],
        "champion": refs["champion"]["content_hash"],
        "cover": refs["cover"]["content_hash"],
        "sisters_formula": refs["sisters_formula"]["content_hash"],
        "host_formula": refs["host_formula"]["content_hash"],
        "assemble_script_hash": scripts["assemble_hash"],
        "assemble_xfade_hash": scripts["xfade_hash"],
        "source_content_hash": spec["source_content_hash"],
        "implementation_hash": implementation_hash,
    }
    if spec["assemble_manifest_mode"] == "segment_set":
        basis["segment_set_manifest"] = refs["segment_set_manifest"]["content_hash"]
    else:
        basis["render_sisters_manifest"] = refs["render_sisters_manifest"]["content_hash"]
        basis["render_host_manifest"] = refs["render_host_manifest"]["content_hash"]
    if "render_sisters_bundle" in refs:
        basis["render_sisters_bundle"] = refs["render_sisters_bundle"]["content_hash"]
        basis["render_host_bundle"] = refs["render_host_bundle"]["content_hash"]
    for field in ("workspace_bundle", "runtime_music_lock"):
        if field in refs:
            basis[field] = refs[field]["content_hash"]
    return input_hash(basis)


def _reel_assemble_tags(spec: dict) -> list[str]:
    return [
        "project:ai-sister",
        "workflow:reel5",
        f"episode:{spec['episode']}",
        "stage:assemble",
        "role:final",
    ]


def _reel_assemble_reusable_output(spec: dict, assemble_input_hash: str) -> list[dict] | None:
    with connect() as conn:
        rows = conn.execute(
            """
            select * from artifacts
            where json_extract(metadata_json, '$.workflow') = 'castle.reel_assemble'
              and json_extract(metadata_json, '$.episode') = ?
              and json_extract(metadata_json, '$.stage') = 'assemble'
              and json_extract(metadata_json, '$.role') = 'final'
              and json_extract(metadata_json, '$.input_hash') = ?
              and artifact_type = 'video'
            order by created_at desc
            """,
            (spec["episode"], assemble_input_hash),
        ).fetchall()
        for row in rows:
            path = artifact_abs_path(conn, row)
            if path and path.is_file():
                metadata = json.loads(row["metadata_json"] or "{}")
                metadata["idempotent_reuse"] = True
                metadata["reused_from_artifact_id"] = row["id"]
                metadata["reused_from_task_id"] = row["producing_task_id"]
                return [{
                    "path": str(path),
                    "name": path.name,
                    "artifact_type": "video",
                    "output_name": "final",
                    "metadata": metadata,
                    "tags": _reel_assemble_tags(spec),
                }]
    return None


def _reel_assemble_metadata(
    spec: dict,
    scripts: dict,
    bed: dict,
    assemble_input_hash: str,
    implementation_hash: str,
) -> dict:
    refs = spec["refs"]
    return {
        "workflow": "castle.reel_assemble",
        "episode": spec["episode"],
        "stage": "assemble",
        "role": "final",
        "theme": _sanitize_error_text(spec["theme"], 120),
        "input_hash": assemble_input_hash,
        "source_content_hash": _sanitize_error_text(spec["source_content_hash"], 160),
        "implementation_hash": implementation_hash,
        "task_schema_version": REEL_ASSEMBLE_TASK_SCHEMA_VERSION,
        "handoff_mode": spec["assemble_manifest_mode"],
        "inputs": {key: _reel_ref_hash_view(value) for key, value in refs.items()},
        "music": {
            "bed_source": bed["source"],
            "bed_basename": _sanitize_error_text(bed["basename"], 120),
            "bed_content_hash": bed["content_hash"],
            "cast": spec["cast"],
            "policy_script_hash": scripts["policy_hash"],
        },
        "settings": {
            "cover_hold": spec["cover_hold"],
            "music_vol": spec["music_vol"],
            "xfade": spec["xfade"],
        },
        "script_hashes": {
            "assemble_reel5": scripts["assemble_hash"],
            "assemble_xfade": scripts["xfade_hash"],
            "music_policy": scripts["policy_hash"],
        },
    }


def run_castle_reel_assemble(payload: dict, work_dir: Path, cancel_file: Path) -> list[dict]:
    source_root = _configured_reel_source_root()
    episode = _episode_id(payload)
    if "workspace_bundle" in payload:
        _require_reel_snapshot_bundle_artifact(
            payload["workspace_bundle"], "workspace_bundle",
            workflow="castle.reel_prepare_render", role="workspace_bundle",
            expected_episode=episode)
    if "render_sisters_bundle" in payload:
        _require_reel_snapshot_bundle_artifact(
            payload["render_sisters_bundle"], "render_sisters_bundle",
            workflow="ai_sister.render_sisters", role="sisters_segments",
            expected_episode=episode)
    if "render_host_bundle" in payload:
        _require_reel_snapshot_bundle_artifact(
            payload["render_host_bundle"], "render_host_bundle",
            workflow="ai_sister.render_host", role="host_segments",
            expected_episode=episode)
    with _reel_workspace_lock(source_root):
        if "workspace_bundle" in payload:
            with _reel_host_audio_authority_lock(
                source_root, episode, exclusive=True,
            ):
                _extract_reel_snapshot_bundle(
                    payload["workspace_bundle"], source_root,
                    "workspace_bundle", expected_episode=episode)
        has_sisters = "render_sisters_bundle" in payload
        has_host = "render_host_bundle" in payload
        if has_sisters != has_host:
            raise ValueError("castle.reel_assemble render segment bundles must be supplied together")
        if has_sisters:
            _extract_reel_snapshot_bundle(
                payload["render_sisters_bundle"], source_root,
                "render_sisters_bundle", expected_episode=episode)
            _extract_reel_snapshot_bundle(
                payload["render_host_bundle"], source_root,
                "render_host_bundle", expected_episode=episode)
        return _run_castle_reel_assemble_locked(payload, work_dir, cancel_file)


def _run_castle_reel_assemble_locked(payload: dict, work_dir: Path, cancel_file: Path) -> list[dict]:
    scripts = _resolve_reel_script_paths()
    spec = _require_reel_assemble_payload(payload, scripts)
    bed = _resolve_reel_music_bed(spec, scripts, cancel_file)
    actual_implementation_hash = _reel_m4_external_implementation_hash(
        scripts["source_root"], "assemble")
    assemble_input_hash = _reel_assemble_input_hash(
        spec, scripts, bed, actual_implementation_hash)
    reusable = (
        _reel_assemble_reusable_output(spec, assemble_input_hash)
        if _runner_internal_reuse_allowed(payload) else None
    )
    if reusable:
        _record_task_event(
            payload,
            "task.idempotent_reuse",
            "Reused reel assemble artifact",
            {"episode": spec["episode"], "input_hash": assemble_input_hash},
        )
        print("CASTLE_PROGRESS " + json.dumps({
            "stage": "assemble",
            "label": "idempotent reuse",
            "episode": spec["episode"],
            "input_hash": assemble_input_hash,
        }, sort_keys=True), flush=True)
        return reusable

    tmp_out = (work_dir / "tmp" / f"assemble-{assemble_input_hash[:16]}-{spec['output_name']}").resolve()
    tmp_out.parent.mkdir(parents=True, exist_ok=True)
    final_out = (spec["output_root"] / spec["output_name"]).resolve()
    final_out = _safe_output_path(str(final_out), "output_name")
    env = _clean_child_env()
    env.update({
        "REEL_SUBDIR": spec["reel_subdir"],
        "TURNLIST_REEL": spec["turnlist_reel_rel"],
        "TURNLIST_HOST": spec["turnlist_host_rel"],
        "COVER_STILL_PNG": str(spec["refs"]["cover"]["path"]),
        "COVER_HOLD": str(spec["cover_hold"]),
        "MUSIC_VOL": str(spec["music_vol"]),
        "XFADE": str(spec["xfade"]),
    })
    # assemble_reel5.sh regenerates $REEL/_concat_reel5.txt and
    # $REEL/reel_cover_hold_av.mp4 on each run; this is shell-owned
    # transitional behavior until the assembler cutover.
    _run_cpu_child(
        [str(scripts["assemble"]), str(bed["path"]), str(tmp_out)],
        scripts["source_root"],
        cancel_file,
        env=env,
        error_label="reel assemble",
    )
    if _cancelled(cancel_file):
        raise KeyboardInterrupt()
    if not tmp_out.is_file():
        raise RuntimeError("reel assemble did not create final video")
    _atomic_copy(tmp_out, final_out)
    metadata = _reel_assemble_metadata(
        spec, scripts, bed, assemble_input_hash, actual_implementation_hash)
    return [{
        # Commit from the task-owned immutable copy.  final_out is retained
        # only as the locked compatibility path for the final listener/QC.
        "path": str(tmp_out),
        "name": final_out.name,
        "artifact_type": "video",
        "output_name": "final",
        "metadata": metadata,
        "tags": _reel_assemble_tags(spec),
    }]


def _safe_single_component(value, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    path = Path(text)
    if path.is_absolute() or ".." in path.parts or len(path.parts) != 1:
        raise ValueError(f"{field} must be a single relative component")
    return text


def _safe_reel_report_name(value, fallback: str) -> str:
    raw = str(value or fallback).strip()
    raw_path = Path(raw)
    if raw_path.is_absolute() or ".." in raw_path.parts or len(raw_path.parts) != 1:
        raise ValueError("report_name must be a single relative filename")
    return _safe_output_name(raw, fallback)


def _resolve_reel_qc_script_paths() -> dict:
    source_root = _trusted_existing_path(
        "CASTLE_REEL5_SOURCE_ROOT",
        Path.home() / ".local/share/web-comfyui/integrations/voice/video-mvp",
        "reel_source_root",
        file=False,
    )
    shell = _trusted_existing_path(
        "CASTLE_REEL_QC_SCRIPT",
        source_root / "qc_reel5.sh",
        "qc_script",
    )
    py = _trusted_existing_path(
        "CASTLE_REEL_QC_PY",
        source_root / "qc_reel5.py",
        "qc_python_script",
    )
    return {
        "source_root": source_root,
        "shell": shell,
        "py": py,
        "shell_hash": _file_sha256(shell),
        "py_hash": _file_sha256(py),
    }


REEL_QC_COVER_ROLES = {
    "cover": "cover",
    "cover_kv": "cover_kv",
    "cover_authority": "cover_authority",
    "cover_bundle": "cover_bundle",
}
REEL_QC_RENDER_BUNDLES = {
    "render_sisters_bundle": ("ai_sister.render_sisters", "sisters_segments"),
    "render_host_bundle": ("ai_sister.render_host", "host_segments"),
}
REEL_QC_RIG_HANDOFF = {
    "qc_rig_sources_manifest": "qc_rig_sources_manifest",
    "qc_rig_bundle": "qc_rig_bundle",
}


def _resolve_reel_qc_cover_refs(payload: dict) -> dict[str, dict]:
    supplied = {field for field in REEL_QC_COVER_ROLES if field in payload}
    if supplied and supplied != set(REEL_QC_COVER_ROLES):
        missing = ", ".join(sorted(set(REEL_QC_COVER_ROLES) - supplied))
        raise ValueError(f"QC immutable cover handoff is incomplete: {missing}")
    refs = {}
    for field, role in REEL_QC_COVER_ROLES.items():
        if field not in payload:
            continue
        ref = _resolve_reel_input_ref(
            _reel_ref_payload(payload.get(field), field), field)
        if ref["kind"] != "artifact":
            raise ValueError(f"{field} must be an adopted Castle artifact")
        _require_reel_artifact_producer(
            ref, field, workflow="castle.reel_cover", role=role)
        refs[field] = ref
    return refs


def _resolve_reel_qc_render_bundle_refs(payload: dict) -> dict[str, dict]:
    supplied = {field for field in REEL_QC_RENDER_BUNDLES if field in payload}
    if supplied and supplied != set(REEL_QC_RENDER_BUNDLES):
        missing = ", ".join(sorted(set(REEL_QC_RENDER_BUNDLES) - supplied))
        raise ValueError(f"QC immutable render handoff is incomplete: {missing}")
    refs = {}
    for field, (workflow, role) in REEL_QC_RENDER_BUNDLES.items():
        if field not in payload:
            continue
        ref = _resolve_reel_input_ref(
            _reel_ref_payload(payload.get(field), field), field)
        if ref["kind"] != "artifact":
            raise ValueError(f"{field} must be an adopted Castle artifact")
        _require_reel_artifact_producer(
            ref, field, workflow=workflow, role=role)
        refs[field] = ref
    return refs


def _resolve_reel_qc_rig_refs(payload: dict) -> dict[str, dict]:
    supplied = {field for field in REEL_QC_RIG_HANDOFF if field in payload}
    if supplied and supplied != set(REEL_QC_RIG_HANDOFF):
        missing = ", ".join(sorted(set(REEL_QC_RIG_HANDOFF) - supplied))
        raise ValueError(f"QC immutable rig handoff is incomplete: {missing}")
    refs = {}
    for field, role in REEL_QC_RIG_HANDOFF.items():
        if field not in payload:
            continue
        ref = _resolve_reel_input_ref(
            _reel_ref_payload(payload.get(field), field), field)
        if ref["kind"] != "artifact":
            raise ValueError(f"{field} must be an adopted Castle artifact")
        _require_reel_artifact_producer(
            ref, field, workflow="castle.reel_prepare_render", role=role)
        refs[field] = ref
    return refs


def _require_reel_qc_payload(payload: dict, scripts: dict) -> dict:
    required = {
        "episode",
        "theme",
        "final",
        "champion",
        "reel_audio_manifest",
        "host_audio_manifest",
        "reel_subdir",
        "reel_aud",
        "host_aud",
        "output_root",
        "source_content_hash",
    }
    optional = {
        "skip_vision", "audio_only", "report_name", "audio_listener_final_receipt",
        "implementation_hash", "workspace_bundle", "champion_bundle",
        "runtime_asr_models_lock",
        *REEL_QC_COVER_ROLES,
        *REEL_QC_RENDER_BUNDLES,
        *REEL_QC_RIG_HANDOFF,
    }
    allowed = required | optional
    unknown = sorted(key for key in payload if not str(key).startswith("_") and key not in allowed)
    if unknown:
        raise ValueError(f"castle.reel_qc unknown fields: {', '.join(unknown)}")
    missing = sorted(key for key in required if key not in payload)
    if missing:
        raise ValueError(f"castle.reel_qc missing fields: {', '.join(missing)}")
    episode = str(payload.get("episode") or "").strip()
    if not re.fullmatch(r"[0-9]+", episode):
        raise ValueError("episode must contain digits only")
    theme = str(payload.get("theme") or "").strip()
    if not theme:
        raise ValueError("theme is required")
    source_content_hash = str(payload.get("source_content_hash") or "").strip()
    if not source_content_hash:
        raise ValueError("source_content_hash is required")
    refs = {}
    for field in ("final", "champion", "reel_audio_manifest", "host_audio_manifest"):
        refs[field] = _resolve_reel_input_ref(_reel_ref_payload(payload.get(field), field), field)
    if refs["champion"]["kind"] == "artifact":
        _require_reel_artifact_producer(
            refs["champion"], "champion",
            workflow="castle.reel_native_stage", role="champion_authority")
        if refs["champion"].get("metadata", {}).get("stage") != "champion_synth":
            raise ValueError("champion must come from native champion_synth")
    if "audio_listener_final_receipt" in payload:
        refs["audio_listener_final_receipt"] = _resolve_reel_input_ref(
            _reel_ref_payload(
                payload.get("audio_listener_final_receipt"),
                "audio_listener_final_receipt",
            ),
            "audio_listener_final_receipt",
        )
    if "workspace_bundle" in payload:
        refs["workspace_bundle"] = _resolve_reel_input_ref(
            _reel_ref_payload(payload.get("workspace_bundle"), "workspace_bundle"),
            "workspace_bundle",
        )
        _require_reel_artifact_producer(
            refs["workspace_bundle"], "workspace_bundle",
            workflow="castle.reel_prepare_render", role="workspace_bundle")
    if "champion_bundle" in payload:
        refs["champion_bundle"] = _resolve_reel_input_ref(
            _reel_ref_payload(payload.get("champion_bundle"), "champion_bundle"),
            "champion_bundle",
        )
        if refs["champion_bundle"]["kind"] != "artifact":
            raise ValueError("champion_bundle must be an adopted Castle artifact")
        _require_reel_artifact_producer(
            refs["champion_bundle"], "champion_bundle",
            workflow="castle.reel_native_stage", role="stage_bundle")
        if refs["champion_bundle"].get("metadata", {}).get("stage") != "champion_synth":
            raise ValueError("champion_bundle must come from native champion_synth")
    refs.update(_resolve_reel_qc_cover_refs(payload))
    refs.update(_resolve_reel_qc_render_bundle_refs(payload))
    refs.update(_resolve_reel_qc_rig_refs(payload))
    if refs["final"]["kind"] == "artifact":
        workflow = refs["final"].get("metadata", {}).get("workflow")
        if workflow and workflow != "castle.reel_assemble":
            raise ValueError("final artifact ref must come from castle.reel_assemble")
    final_path = refs["final"]["path"]
    source_root = scripts["source_root"]
    if "runtime_asr_models_lock" in payload:
        refs["runtime_asr_models_lock"], _ = _require_reel_runtime_group_ref(
            payload["runtime_asr_models_lock"], "asr_models", source_root,
            verify_source=True)
    try:
        final_mp4 = _relative_to_source(final_path, source_root, "final")
    except ValueError:
        final_mp4 = str(final_path)
    report_probe_hash = input_hash({
        "episode": episode,
        "final": refs["final"]["content_hash"],
        "champion": refs["champion"]["content_hash"],
        "reel_audio_manifest": refs["reel_audio_manifest"]["content_hash"],
        "host_audio_manifest": refs["host_audio_manifest"]["content_hash"],
    })[:16]
    report_name = _safe_reel_report_name(payload.get("report_name"), f"qc_report_{episode}-{report_probe_hash}.json")
    return {
        "episode": episode,
        "theme": theme,
        "refs": refs,
        "final_mp4": final_mp4,
        "reel_subdir": _safe_single_component(payload.get("reel_subdir"), "reel_subdir"),
        "reel_aud": _safe_single_component(payload.get("reel_aud"), "reel_aud"),
        "host_aud": _safe_single_component(payload.get("host_aud"), "host_aud"),
        "output_root": _safe_output_path(payload.get("output_root"), "output_root"),
        "source_content_hash": source_content_hash,
        "implementation_hash": str(payload.get("implementation_hash") or "").strip(),
        "skip_vision": _reel_bool(payload.get("skip_vision"), False),
        "audio_only": _reel_bool(payload.get("audio_only"), False),
        "report_name": report_name,
        "task_schema_version": REEL_QC_TASK_SCHEMA_VERSION,
    }


def _reel_qc_input_hash(
    spec: dict,
    scripts: dict,
    implementation_hash: str,
) -> str:
    payload = {
        "episode": spec["episode"],
        "theme": spec["theme"],
        "reel_subdir": spec["reel_subdir"],
        "reel_aud": spec["reel_aud"],
        "host_aud": spec["host_aud"],
        "skip_vision": spec["skip_vision"],
        "audio_only": spec["audio_only"],
        "source_content_hash": spec["source_content_hash"],
        "implementation_hash": implementation_hash,
    }
    payload.update(spec["refs"])
    return qc_evidence_input_hash(
        payload,
        qc_reel5_sh_hash=scripts["shell_hash"],
        qc_reel5_py_hash=scripts["py_hash"],
    )


def _reel_qc_tags(spec: dict) -> list[str]:
    return [
        "project:ai-sister",
        "workflow:reel5",
        f"episode:{spec['episode']}",
        "stage:qc",
        "role:qc",
    ]


def _reel_qc_cost(spec: dict, request_count: object) -> dict:
    try:
        return canonical_qc_cost(
            audio_only=spec["audio_only"],
            skip_vision=spec["skip_vision"],
            request_count=request_count,
        )
    except ValueError as error:
        raise RuntimeError(str(error)) from error


def _validated_reel_qc_judge_authority(raw: dict) -> tuple[dict, str]:
    authority = raw.get("qc_judge_authority")
    fingerprint = raw.get("qc_judge_authority_fingerprint")
    if not isinstance(authority, dict):
        raise RuntimeError("reel QC judge authority is missing or malformed")
    if not isinstance(fingerprint, str) or not re.fullmatch(
            r"[0-9a-f]{64}", fingerprint):
        raise RuntimeError("reel QC judge authority fingerprint is missing or malformed")

    policy_version = authority.get("policy_version")
    if not isinstance(policy_version, str) or not policy_version:
        raise RuntimeError("reel QC judge authority policy_version is missing or malformed")
    policy = {
        key: value for key, value in authority.items()
        if key != "policy_version"
    }
    calculated_fingerprint = hashlib.sha256(json.dumps(
        policy,
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    if fingerprint != calculated_fingerprint:
        raise RuntimeError("reel QC judge authority fingerprint mismatch")
    if not policy_version.endswith("+" + fingerprint):
        raise RuntimeError("reel QC judge authority policy_version fingerprint mismatch")

    # Castle is the release boundary.  A self-consistent report from a changed
    # provider, model, endpoint or transaction policy is not canonical release
    # evidence until this task contract is intentionally revised.
    canonical_authority = json.dumps(
        REEL_QC_JUDGE_AUTHORITY,
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    supplied_authority = json.dumps(
        authority,
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    if (
        supplied_authority != canonical_authority
        or fingerprint != REEL_QC_JUDGE_AUTHORITY_FINGERPRINT
    ):
        raise RuntimeError("reel QC judge authority is noncanonical")
    return dict(REEL_QC_JUDGE_AUTHORITY), fingerprint


def _validated_reel_qc_vision_authority(raw: dict) -> tuple[dict, str]:
    authority = raw.get("qc_vision_authority")
    fingerprint = raw.get("qc_vision_authority_fingerprint")
    if not isinstance(authority, dict) or not isinstance(fingerprint, str):
        raise RuntimeError("reel QC vision authority is missing or malformed")
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise RuntimeError("reel QC vision authority fingerprint is malformed")
    supplied = json.dumps(
        authority, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    canonical = json.dumps(
        REEL_QC_VISION_AUTHORITY,
        ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if (
        supplied != canonical
        or fingerprint != REEL_QC_VISION_AUTHORITY_FINGERPRINT
        or not str(authority.get("policy_version") or "").endswith(
            "+" + fingerprint)
    ):
        raise RuntimeError("reel QC vision authority is noncanonical")
    return dict(REEL_QC_VISION_AUTHORITY), fingerprint


def _validated_reel_qc_quality_authority(raw: dict) -> tuple[dict, str]:
    authority = raw.get("qc_quality_authority")
    fingerprint = raw.get("qc_quality_authority_fingerprint")
    if (
        authority != REEL_QC_QUALITY_AUTHORITY
        or fingerprint != REEL_QC_QUALITY_AUTHORITY_FINGERPRINT
        or not str((authority or {}).get("policy_version") or "").endswith(
            "+" + str(fingerprint or ""))
    ):
        raise RuntimeError("reel QC quality authority is noncanonical")
    return dict(REEL_QC_QUALITY_AUTHORITY), str(fingerprint)


def _validated_reel_qc_quality_input(raw: dict, spec: dict) -> dict:
    value = raw.get("quality_input")
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "final_mp4_sha256", "size_bytes",
        "duration_seconds", "width", "height", "video_stream",
        "audio_stream",
    }:
        raise RuntimeError("reel QC quality input is malformed")
    final_hash = str(spec["refs"]["final"].get("content_hash") or "")
    if (
        value.get("schema_version") != "reel-final-media-integrity-v1"
        or value.get("final_mp4_sha256") != final_hash
        or isinstance(value.get("size_bytes"), bool)
        or not isinstance(value.get("size_bytes"), int)
        or value["size_bytes"] <= 0
        or isinstance(value.get("duration_seconds"), bool)
        or not isinstance(value.get("duration_seconds"), (int, float))
        or value["duration_seconds"] <= 0
        or isinstance(value.get("width"), bool)
        or not isinstance(value.get("width"), int)
        or value["width"] <= 0
        or isinstance(value.get("height"), bool)
        or not isinstance(value.get("height"), int)
        or value["height"] <= 0
        or value.get("video_stream") is not True
        or value.get("audio_stream") is not True
    ):
        raise RuntimeError("reel QC quality input differs from the final artifact")
    return dict(value)


def _compact_reel_qc_cover_authority(raw: dict, spec: dict) -> dict:
    refs = spec["refs"]
    release = raw.get("release_authority")
    cover = release.get("cover") if isinstance(release, dict) else None
    if not isinstance(cover, dict):
        raise RuntimeError("reel QC release authority has no cover evidence")
    expected_keys = {
        "schema", "debate_id", "theme", "mode", "prompt_sha256",
        "authority", "metadata", "key_visual", "final_png",
    }
    if set(cover) != expected_keys:
        raise RuntimeError("reel QC cover authority fields are noncanonical")
    if (
        cover.get("schema") != COVER_AUTHORITY_SCHEMA
        or str(cover.get("debate_id") or "") != spec["episode"]
        or cover.get("theme") != spec["theme"]
        or cover.get("mode") != CANONICAL_COVER_MODE
        or not re.fullmatch(r"[0-9a-f]{64}", str(cover.get("prompt_sha256") or ""))
    ):
        raise RuntimeError("reel QC cover authority identity/mode is noncanonical")

    compact_records = {}
    for field in ("authority", "metadata", "key_visual", "final_png"):
        record = cover.get(field)
        required = {"path", "sha256"} if field == "authority" else {
            "path", "sha256", "bytes"}
        if not isinstance(record, dict) or set(record) != required:
            raise RuntimeError(f"reel QC cover {field} authority is malformed")
        digest = str(record.get("sha256") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise RuntimeError(f"reel QC cover {field} digest is malformed")
        if field != "authority" and (
            isinstance(record.get("bytes"), bool)
            or not isinstance(record.get("bytes"), int)
            or record["bytes"] <= 0
        ):
            raise RuntimeError(f"reel QC cover {field} byte count is malformed")
        compact_records[field] = digest

    cover_artifact_roles = ("cover", "cover_kv", "cover_authority")
    if not all(field in refs for field in cover_artifact_roles):
        raise RuntimeError("reel QC immutable cover artifact handoff is incomplete")
    audits = []
    for field in cover_artifact_roles:
        try:
            audits.append(validate_cover_audit(
                refs[field].get("metadata", {}).get("cover_audit")))
        except (TypeError, ValueError) as error:
            raise RuntimeError(
                f"reel QC {field} artifact has no canonical cover audit") from error
    if any(audit != audits[0] for audit in audits[1:]):
        raise RuntimeError("reel QC cover artifact audits disagree")
    audit = audits[0]
    try:
        validate_cover_audit(
            audit,
            expected_final_sha256=refs["cover"]["content_hash"],
            expected_metadata_sha256=refs["cover_kv"]["content_hash"],
            expected_authority_sha256=refs["cover_authority"]["content_hash"],
        )
    except (TypeError, ValueError) as error:
        raise RuntimeError("reel QC cover artifacts differ from compact audit") from error
    if (
        compact_records["authority"] != audit["authority_sha256"]
        or compact_records["metadata"] != audit["metadata_sha256"]
        or compact_records["key_visual"] != audit["key_visual_sha256"]
        or compact_records["final_png"] != audit["final_png_sha256"]
        or cover["prompt_sha256"] != audit["prompt_sha256"]
    ):
        raise RuntimeError("reel QC cover release evidence differs from artifact audit")
    return {
        "schema": cover["schema"],
        "debate_id": spec["episode"],
        "theme": spec["theme"],
        "mode": cover["mode"],
        "prompt_sha256": cover["prompt_sha256"],
        "authority_sha256": compact_records["authority"],
        "metadata_sha256": compact_records["metadata"],
        "key_visual_sha256": compact_records["key_visual"],
        "final_png_sha256": compact_records["final_png"],
        "cover_audit": audit,
    }


def _compact_reel_qc_release_authority(raw: dict, spec: dict) -> dict:
    release = raw.get("release_authority")
    expected_top = {
        "schema", "sister_audio_signature", "host_audio_signature",
        "authority_files", "cover",
    }
    if not isinstance(release, dict) or set(release) != expected_top:
        raise RuntimeError("reel QC release authority is incomplete or noncanonical")
    if release.get("schema") != "reel5_release_authority_evidence_v2_20260711":
        raise RuntimeError("reel QC release authority schema is noncanonical")
    for field in ("sister_audio_signature", "host_audio_signature"):
        if re.fullmatch(r"[0-9a-f]{64}", str(release.get(field) or "")) is None:
            raise RuntimeError(f"reel QC {field} is malformed")

    episode = spec["episode"]
    expected_files = {
        f"turnlist_reel5_{episode}.json",
        f"{spec['reel_aud']}/.main_synth_policy.json",
        f"{spec['reel_aud']}/spoken.json",
        f"react_beats_peer_{episode}.json",
        f"turnlist_host_{episode}.json",
        f"{spec['host_aud']}/host_audio_manifest.json",
        f"react_beats_host_{episode}.json",
        f"champion_stub_{episode}.json",
        f"champion_{episode}.json",
        "champion_lines.json",
    }
    authority_files = release.get("authority_files")
    if not isinstance(authority_files, dict) or set(authority_files) != expected_files:
        raise RuntimeError("reel QC release authority file set is noncanonical")
    normalized_files = {}
    for relative, digest in authority_files.items():
        parts = str(relative).replace("\\", "/").split("/")
        if (
            not relative
            or str(relative).startswith("/")
            or any(part in {"", ".", ".."} for part in parts)
            or re.fullmatch(r"[0-9a-f]{64}", str(digest or "")) is None
        ):
            raise RuntimeError("reel QC release authority file record is unsafe")
        normalized_files[str(relative)] = str(digest)
    compact_cover = _compact_reel_qc_cover_authority(raw, spec)
    return {
        "schema": QC_RELEASE_AUTHORITY_SCHEMA,
        "source_schema": release["schema"],
        "sister_audio_signature": release["sister_audio_signature"],
        "host_audio_signature": release["host_audio_signature"],
        "authority_files": {
            key: normalized_files[key] for key in sorted(normalized_files)
        },
        "authority_files_sha256": qc_contract_sha256(normalized_files),
        "cover": compact_cover,
    }


def _sanitize_reel_qc_report(
    raw: dict,
    spec: dict,
    qc_input_hash: str,
    orchestrator_input_hash: str,
) -> dict:
    if not isinstance(raw, dict):
        raise RuntimeError("reel QC report is not a JSON object")
    judge_authority, judge_authority_fingerprint = (
        _validated_reel_qc_judge_authority(raw)
    )
    vision_authority, vision_authority_fingerprint = (
        _validated_reel_qc_vision_authority(raw)
    )
    quality_authority, quality_authority_fingerprint = (
        _validated_reel_qc_quality_authority(raw)
    )
    if judge_authority != quality_authority or vision_authority != quality_authority:
        raise RuntimeError("reel QC authority aliases authorize different transactions")
    try:
        quality_assessment = validate_quality_assessment(
            raw.get("quality_assessment"))
    except ValueError as error:
        raise RuntimeError(str(error)) from error
    quality_input = _validated_reel_qc_quality_input(raw, spec)
    raw_checks = raw.get("checks")
    try:
        verdict = reel_qc_checks_verdict(
            raw_checks,
            audio_only=spec["audio_only"],
            skip_vision=spec["skip_vision"],
        )
    except ValueError as error:
        raise RuntimeError(str(error)) from error
    if str(raw.get("verdict") or "").strip().upper() != verdict:
        raise RuntimeError("reel QC verdict differs from canonical check levels")
    if quality_assessment["verdict"] != verdict:
        raise RuntimeError("reel QC verdict differs from its KPI assessment")
    if (spec["audio_only"] or spec["skip_vision"]) and verdict == "PASS":
        raise RuntimeError("reel QC development mode cannot authorize release")
    quality_checks = {
        row.get("check"): row.get("level") for row in raw_checks
        if isinstance(row, dict)
    }
    if quality_checks.get("post_render.quality-score") != verdict:
        raise RuntimeError("reel QC quality check differs from its assessment")
    expected_completion = canonical_draft_completion(verdict)
    if raw.get("draft_completion") != expected_completion:
        raise RuntimeError(
            "reel QC draft completion receipt differs from canonical verdict")
    if (
        str(raw.get("debate_id") or "") != spec["episode"]
        or str(raw.get("theme") or "") != spec["theme"]
    ):
        raise RuntimeError("reel QC report identity differs from task input")
    checks = []
    for item in raw_checks:
        checks.append({
            "check": str(item["check"]),
            "level": str(item["level"]),
            "detail": _sanitize_error_text(item.get("detail"), 500),
        })
    release_authority = _compact_reel_qc_release_authority(raw, spec)
    report = {
        "debate_id": spec["episode"],
        "episode": spec["episode"],
        "theme": spec["theme"],
        "verdict": verdict,
        "checks": checks,
        "checks_truncated": False,
        "input_hash": qc_input_hash,
        "orchestrator_input_hash": orchestrator_input_hash,
        "task_schema_version": REEL_QC_TASK_SCHEMA_VERSION,
        "source_content_hash": _sanitize_error_text(spec["source_content_hash"], 160),
        "cost": _reel_qc_cost(spec, raw.get("vision_request_count")),
        "quality_assessment": quality_assessment,
        "quality_input": quality_input,
        "qc_quality_authority": quality_authority,
        "qc_quality_authority_fingerprint": quality_authority_fingerprint,
        "qc_judge_authority": judge_authority,
        "qc_judge_authority_fingerprint": judge_authority_fingerprint,
        "qc_vision_authority": vision_authority,
        "qc_vision_authority_fingerprint": vision_authority_fingerprint,
        "release_authority": release_authority,
        "draft_completion": expected_completion,
    }
    return report


def _reel_qc_metadata(
    spec: dict,
    scripts: dict,
    qc_input_hash: str,
    implementation_hash: str,
    report: dict,
) -> dict:
    metadata = {
        "workflow": "castle.reel_qc",
        "episode": spec["episode"],
        "stage": "qc",
        "role": "qc",
        "theme": _sanitize_error_text(spec["theme"], 120),
        "input_hash": qc_input_hash,
        "orchestrator_input_hash": report["orchestrator_input_hash"],
        "source_content_hash": _sanitize_error_text(spec["source_content_hash"], 160),
        "implementation_hash": implementation_hash,
        "task_schema_version": REEL_QC_TASK_SCHEMA_VERSION,
        "verdict": report["verdict"],
        "draft_completion": report["draft_completion"],
        "review_required": report["draft_completion"]["review_required"],
        "release_eligible": report["draft_completion"]["release_eligible"],
        "cost": report["cost"],
        "quality_assessment": report["quality_assessment"],
        "quality_input": report["quality_input"],
        "qc_quality_authority": report["qc_quality_authority"],
        "qc_quality_authority_fingerprint": report[
            "qc_quality_authority_fingerprint"
        ],
        "qc_judge_authority": report["qc_judge_authority"],
        "qc_judge_authority_fingerprint": report[
            "qc_judge_authority_fingerprint"
        ],
        "qc_vision_authority": report["qc_vision_authority"],
        "qc_vision_authority_fingerprint": report[
            "qc_vision_authority_fingerprint"
        ],
        "inputs": {key: _reel_ref_hash_view(value) for key, value in spec["refs"].items()},
        "settings": {
            "skip_vision": spec["skip_vision"],
            "audio_only": spec["audio_only"],
        },
        "script_hashes": {
            "qc_reel5_sh": scripts["shell_hash"],
            "qc_reel5_py": scripts["py_hash"],
        },
    }
    metadata["release_authority"] = report["release_authority"]
    metadata["cover_audit"] = report["release_authority"]["cover"]["cover_audit"]
    return metadata


def _reel_qc_reusable_output(
    spec: dict,
    qc_input_hash: str,
    orchestrator_input_hash: str,
) -> list[dict] | None:
    with connect() as conn:
        rows = conn.execute(
            """
            select * from artifacts
            where json_extract(metadata_json, '$.workflow') = 'castle.reel_qc'
              and json_extract(metadata_json, '$.episode') = ?
              and json_extract(metadata_json, '$.stage') = 'qc'
              and json_extract(metadata_json, '$.role') = 'qc'
              and json_extract(metadata_json, '$.input_hash') = ?
              and json_extract(metadata_json, '$.orchestrator_input_hash') = ?
              and artifact_type = 'json'
            order by created_at desc
            """,
            (spec["episode"], qc_input_hash, orchestrator_input_hash),
        ).fetchall()
        for row in rows:
            path = artifact_abs_path(conn, row)
            if not path or not path.is_file():
                continue
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            metadata = json.loads(row["metadata_json"] or "{}")
            if (
                metadata.get("verdict") == "FAIL"
                or metadata.get("release_eligible") is not True
            ):
                continue
            metadata["idempotent_reuse"] = True
            metadata["reused_from_artifact_id"] = row["id"]
            metadata["reused_from_task_id"] = row["producing_task_id"]
            return [{
                "path": str(path),
                "name": path.name,
                "artifact_type": "json",
                "output_name": "qc",
                "metadata": metadata,
                "tags": _reel_qc_tags(spec),
            }]
    return None


def _reel_qc_billing_marker(output: str) -> str | None:
    lowered = output.lower()
    if "vision call failed" not in lowered:
        return None
    for marker in REEL_QC_BILLING_MARKERS:
        if marker in lowered:
            return marker
    match = re.search(r"\b(401|403|429)\b", lowered)
    if match:
        return match.group(1)
    return None


def _run_reel_qc_child(cmd: list[str], cwd: Path, cancel_file: Path, env: dict) -> str:
    try:
        return _run_cpu_child(cmd, cwd, cancel_file, env=env, error_label="reel QC")
    except RuntimeError as error:
        output = getattr(error, "child_output", str(error))
        marker = _reel_qc_billing_marker(output)
        if marker:
            raise RuntimeError(f"billing_block: {marker}: {_sanitize_error_tail(output)}") from error
        raise


def _resolve_reel_render_script_paths() -> dict:
    source_root = _trusted_existing_path(
        "CASTLE_REEL5_SOURCE_ROOT",
        Path.home() / ".local/share/web-comfyui/integrations/voice/video-mvp",
        "reel_source_root",
        file=False,
    )
    highlight = _trusted_existing_path(
        "CASTLE_REEL_RENDER_HIGHLIGHT_SCRIPT",
        source_root / "render_highlight.sh",
        "render_highlight_script",
    )
    host = _trusted_existing_path(
        "CASTLE_REEL_RENDER_HOST_SCRIPT",
        source_root / "render_host.sh",
        "render_host_script",
    )
    script_names = {
        "build_puppet": "build_puppet.py",
        "react_timing": "react_timing.py",
        "mux_champion": "mux_champion.py",
        "champion_authority": "champion_authority.py",
        "audio_ready_authority": "audio_ready_authority.py",
        "assemble_xfade": "assemble_xfade.py",
    }
    scripts = {
        "source_root": source_root,
        "render_highlight": highlight,
        "render_host": host,
        "render_highlight_hash": _file_sha256(highlight),
        "render_host_hash": _file_sha256(host),
    }
    for key, name in script_names.items():
        path = _trusted_existing_path(f"CASTLE_REEL_RENDER_{key.upper()}_SCRIPT", source_root / name, key)
        scripts[key] = path
        scripts[f"{key}_hash"] = _file_sha256(path)
    return scripts


def _run_reel_gpu_child(cmd: list[str], cwd: Path, cancel_file: Path, *, env: dict, error_label: str) -> dict:
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=True,
    )
    assert proc.stdout is not None
    queue_lines: list[str] = []
    captured: list[str] = []
    lines: queue.Queue = queue.Queue()
    reader = threading.Thread(target=_reader_thread, args=(proc.stdout, lines), daemon=True)
    reader.start()
    try:
        while proc.poll() is None:
            if _cancelled(cancel_file):
                _terminate_process_group(proc)
                raise KeyboardInterrupt()
            try:
                line = lines.get(timeout=0.2)
            except queue.Empty:
                continue
            if line is None:
                continue
            captured.append(line.rstrip()[:2000])
            print(line, end="", flush=True)
            if line.startswith("[gpu-queue]"):
                queue_lines.append(line.rstrip()[:1000])
        rc = proc.wait()
        drain_deadline = time.monotonic() + 1.0
        while time.monotonic() < drain_deadline:
            try:
                line = lines.get(timeout=0.05)
            except queue.Empty:
                break
            if line is None:
                break
            captured.append(line.rstrip()[:2000])
            print(line, end="", flush=True)
            if line.startswith("[gpu-queue]"):
                queue_lines.append(line.rstrip()[:1000])
        if _cancelled(cancel_file):
            raise KeyboardInterrupt()
        output = "\n".join(captured)
        if rc != 0:
            error = RuntimeError(f"{error_label} exited {rc}: {_sanitize_error_tail(output)}")
            error.child_output = output
            error.returncode = rc
            raise error
    finally:
        if proc.poll() is None:
            _terminate_process_group(proc)
        reader.join(timeout=1)
    if not queue_lines:
        error = RuntimeError(f"{error_label} did not observe required [gpu-queue] marker")
        error.child_output = "\n".join(captured)
        raise error
    return {
        "gpu_queue_cmd_used": True,
        "gpu_queue_marker_observed": True,
        "gpu_queue_marker_count": len(queue_lines),
    }


def _reel_render_failure_kind(output: str) -> str:
    text = output.lower()
    if "timeout" in text and "gpu" in text or "queue timeout" in text or "wait exceeded" in text:
        return "gpu_queue_timeout"
    if "cuda out of memory" in text or "outofmemory" in text or "allocation failed" in text or "gpu process crash" in text or "resource exhausted" in text:
        return "gpu_oom"
    if "render failed" in text:
        return "render_seek_failed"
    if "missing " in text or "no such file" in text or "not found" in text:
        return "missing_input"
    return "script_error"


def _reel_render_ref_fields_for_stage(stage: str) -> tuple[str, ...]:
    if stage == "render_sisters":
        return ("turnlist_reel", "audio_manifest", "peer_beats", "rig_manifest", "sfx_manifest")
    return (
        "turnlist_reel",
        "turnlist_host",
        "host_audio_manifest",
        "peer_beats",
        "host_beats",
        "sister_rig_manifest",
        "host_rig_manifest",
        "champion_lines",
        "champion_authority",
        "audio_ready",
    )


def _require_manifest_payload(path: Path, field: str, *, source_root: Path, required_roles: tuple[str, ...] = ()) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        raise ValueError(f"{field} manifest is not valid JSON") from error
    if not isinstance(raw, dict):
        raise ValueError(f"{field} manifest must be a JSON object")
    if raw.get("schema_version") != REEL_RENDER_INPUT_MANIFEST_SCHEMA_VERSION:
        raise ValueError(f"{field} unknown manifest schema_version")
    entries = raw.get("entries")
    if not isinstance(entries, list):
        raise ValueError(f"{field} manifest entries must be an array")
    clean_entries = []
    seen_roles = set()
    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"{field} manifest entry {idx} must be an object")
        allowed = {"role", "path", "sha256", "size", "speaker", "idx"}
        unknown = sorted(key for key in entry if key not in allowed)
        if unknown:
            raise ValueError(f"{field} manifest entry {idx} unknown keys: {', '.join(unknown)}")
        role = str(entry.get("role") or "").strip()
        rel = _safe_reel_relative(entry.get("path"), f"{field}.entries[{idx}].path")
        sha = str(entry.get("sha256") or "").strip()
        size = entry.get("size")
        if not role or not re.fullmatch(r"[0-9a-f]{64}", sha) or not isinstance(size, int) or size < 0:
            raise ValueError(f"{field} manifest entry {idx} has invalid role/path/sha256/size")
        entry_path = (source_root / rel).resolve()
        try:
            entry_path.relative_to(source_root.resolve())
        except ValueError as error:
            raise ValueError(f"{field} manifest entry {idx} path escapes source root: {Path(rel).name}") from error
        if not entry_path.is_file():
            raise ValueError(f"{field} manifest entry {idx} file missing: {Path(rel).name}")
        actual_sha = _file_sha256(entry_path)
        actual_size = entry_path.stat().st_size
        if actual_sha != sha or actual_size != size:
            raise ValueError(f"{field} manifest entry {idx} file hash/size mismatch: {entry_path.name}")
        clean_entries.append({
            "role": role,
            "path": rel,
            "sha256": sha,
            "size": size,
            **({"speaker": str(entry.get("speaker"))} if entry.get("speaker") is not None else {}),
            **({"idx": int(entry.get("idx"))} if isinstance(entry.get("idx"), int) else {}),
        })
        seen_roles.add(role)
    missing = sorted(set(required_roles) - seen_roles)
    if missing:
        raise ValueError(f"{field} manifest missing roles: {', '.join(missing)}")
    return {
        "schema_version": REEL_RENDER_INPUT_MANIFEST_SCHEMA_VERSION,
        "dir": str(raw.get("dir") or "").strip(),
        "entries": clean_entries,
    }


def _manifest_role_path(manifest: dict, role: str) -> str:
    for entry in manifest["entries"]:
        if entry["role"] == role:
            return entry["path"]
    raise ValueError(f"manifest missing role: {role}")


def _manifest_dir(manifest: dict, fallback_entry_role: str) -> str:
    if manifest.get("dir"):
        return _safe_reel_relative(manifest["dir"], "manifest.dir")
    return str(Path(_manifest_role_path(manifest, fallback_entry_role)).parent)


def _normalize_render_settings(value, *, stage: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError("render_settings must be an object")
    if stage == "render_sisters":
        allowed = {"render_fps", "render_concurrency", "video_preset", "video_crf", "xfade", "music", "skip_audio"}
        required = allowed
    else:
        allowed = {"render_fps", "render_concurrency", "video_preset", "video_crf", "xfade", "champ_seed"}
        required = allowed
    unknown = sorted(key for key in value if key not in allowed)
    if unknown:
        raise ValueError(f"render_settings unknown fields: {', '.join(unknown)}")
    missing = sorted(key for key in required if key not in value)
    if missing:
        raise ValueError(f"render_settings missing fields: {', '.join(missing)}")
    try:
        settings = {
            "render_fps": int(value["render_fps"]),
            "render_concurrency": int(value["render_concurrency"]),
            "video_preset": str(value["video_preset"]).strip(),
            "video_crf": int(value["video_crf"]),
            "xfade": float(value["xfade"]),
        }
    except (TypeError, ValueError) as error:
        raise ValueError("render_settings values have invalid types") from error
    if settings["render_fps"] <= 0 or settings["render_concurrency"] <= 0 or settings["video_crf"] < 0 or not settings["video_preset"]:
        raise ValueError("render_settings values are out of range")
    if stage == "render_sisters":
        settings["music"] = _reel_bool(value.get("music"), False)
        settings["skip_audio"] = _reel_bool(value.get("skip_audio"), False)
        if settings["music"]:
            raise ValueError("ai_sister.render_sisters requires render_settings.music=false")
        if not settings["skip_audio"]:
            raise ValueError("ai_sister.render_sisters requires render_settings.skip_audio=true")
    else:
        settings["champ_seed"] = str(value["champ_seed"]).strip()
        if not settings["champ_seed"]:
            raise ValueError("render_settings.champ_seed is required")
    return settings


def _require_reel_render_payload(payload: dict, scripts: dict, *, stage: str) -> dict:
    if "render_idxs" in payload:
        raise ValueError("render_idxs is a reserved field for future partial-render support")
    if "idxs" in payload:
        raise ValueError("idxs is a reserved field for future partial-render support")
    if stage == "render_sisters":
        required = {"episode", "theme", "output_root", "reel_subdir", "turnlist_reel", "audio_manifest", "peer_beats", "rig_manifest", "sfx_manifest", "render_settings", "source_content_hash"}
        optional = {
            "output_name", "rough_mode", "snapshot_segments", "implementation_hash",
            "workspace_bundle", "runtime_sfx_sources_lock", "runtime_sfx_outputs_lock",
        }
    else:
        required = {"episode", "theme", "output_root", "reel_subdir", "turnlist_reel", "turnlist_host", "host_audio_manifest", "peer_beats", "host_beats", "sister_rig_manifest", "host_rig_manifest", "champion_lines", "champion_authority", "audio_ready", "render_settings", "source_content_hash"}
        optional = {
            "output_name", "adopt_proofs", "snapshot_segments", "implementation_hash",
            "host_sfx_manifest", "workspace_bundle",
            "runtime_sfx_sources_lock", "runtime_sfx_outputs_lock",
        }
    allowed = required | optional
    unknown = sorted(key for key in payload if not str(key).startswith("_") and key not in allowed)
    if unknown:
        raise ValueError(f"ai_sister.{stage} unknown fields: {', '.join(unknown)}")
    missing = sorted(key for key in required if key not in payload)
    if missing:
        raise ValueError(f"ai_sister.{stage} missing fields: {', '.join(missing)}")
    episode = str(payload.get("episode") or "").strip()
    if not re.fullmatch(r"[0-9]+", episode):
        raise ValueError("episode must contain digits only")
    theme = str(payload.get("theme") or "").strip()
    if not theme:
        raise ValueError("theme is required")
    source_content_hash = str(payload.get("source_content_hash") or "").strip()
    if not source_content_hash:
        raise ValueError("source_content_hash is required")
    refs = {
        field: _resolve_reel_input_ref(_reel_ref_payload(payload.get(field), field), field)
        for field in _reel_render_ref_fields_for_stage(stage)
    }
    source_root = scripts["source_root"]
    if "workspace_bundle" in payload:
        refs["workspace_bundle"] = _resolve_reel_input_ref(
            _reel_ref_payload(payload["workspace_bundle"], "workspace_bundle"),
            "workspace_bundle",
        )
        _require_reel_artifact_producer(
            refs["workspace_bundle"], "workspace_bundle",
            workflow="castle.reel_prepare_render", role="workspace_bundle")
    supplied_runtime_groups = {
        group for group in ("sfx_sources", "sfx_outputs")
        if f"runtime_{group}_lock" in payload
    }
    if supplied_runtime_groups not in (set(), {"sfx_sources", "sfx_outputs"}):
        raise ValueError("render runtime SFX group locks must be supplied together")
    for group in sorted(supplied_runtime_groups):
        field = f"runtime_{group}_lock"
        refs[field], _ = _require_reel_runtime_group_ref(
            payload[field], group, source_root, verify_source=True)
    if stage == "render_host" and "host_sfx_manifest" in payload:
        refs["host_sfx_manifest"] = _resolve_reel_input_ref(
            _reel_ref_payload(payload["host_sfx_manifest"], "host_sfx_manifest"),
            "host_sfx_manifest",
        )
    for field in _reel_render_ref_fields_for_stage(stage):
        ref = refs[field]
        if not field.endswith("_manifest"):
            _relative_to_source(ref["path"], source_root, field)
    manifests = {}
    if stage == "render_sisters":
        manifests["audio_manifest"] = _require_manifest_payload(refs["audio_manifest"]["path"], "audio_manifest", source_root=source_root, required_roles=("spoken", "timing"))
        manifests["rig_manifest"] = _require_manifest_payload(refs["rig_manifest"]["path"], "rig_manifest", source_root=source_root)
        manifests["sfx_manifest"] = _require_manifest_payload(refs["sfx_manifest"]["path"], "sfx_manifest", source_root=source_root, required_roles=("push_cue", "boo_cue", "push_pop", "boo_pop", "push_baby", "boo_baby"))
        default_output = f"render_sisters_{episode}.json"
    else:
        manifests["host_audio_manifest"] = _require_manifest_payload(refs["host_audio_manifest"]["path"], "host_audio_manifest", source_root=source_root, required_roles=("intro", "outro"))
        manifests["sister_rig_manifest"] = _require_manifest_payload(refs["sister_rig_manifest"]["path"], "sister_rig_manifest", source_root=source_root)
        manifests["host_rig_manifest"] = _require_manifest_payload(refs["host_rig_manifest"]["path"], "host_rig_manifest", source_root=source_root)
        if "host_sfx_manifest" in refs:
            manifests["host_sfx_manifest"] = _require_manifest_payload(
                refs["host_sfx_manifest"]["path"], "host_sfx_manifest",
                source_root=source_root, required_roles=("pop",))
        default_output = f"render_host_{episode}.json"
    return {
        "episode": episode,
        "theme": theme,
        "output_root": _safe_output_path(payload.get("output_root"), "output_root"),
        "reel_subdir": _safe_single_component(payload.get("reel_subdir"), "reel_subdir"),
        "refs": refs,
        "manifests": manifests,
        "render_settings": _normalize_render_settings(payload.get("render_settings"), stage=stage),
        "source_content_hash": source_content_hash,
        "output_name": _safe_reel_output_name(payload.get("output_name"), default_output),
        "adopt_proofs": _reel_bool(payload.get("adopt_proofs"), False),
        "rough_mode": _reel_bool(payload.get("rough_mode"), False),
        "snapshot_segments": _reel_bool(payload.get("snapshot_segments"), False),
        "implementation_hash": str(payload.get("implementation_hash") or "").strip(),
        "task_schema_version": REEL_RENDER_TASK_SCHEMA_VERSION,
    }


def _reel_render_script_hashes(scripts: dict, stage: str) -> dict:
    if stage == "render_sisters":
        return {
            "render_highlight": scripts["render_highlight_hash"],
            "build_puppet": scripts["build_puppet_hash"],
            "react_timing": scripts["react_timing_hash"],
        }
    return {
        "render_host": scripts["render_host_hash"],
        "build_puppet": scripts["build_puppet_hash"],
        "mux_champion": scripts["mux_champion_hash"],
        "champion_authority": scripts["champion_authority_hash"],
        "audio_ready_authority": scripts["audio_ready_authority_hash"],
        "react_timing": scripts["react_timing_hash"],
        "assemble_xfade": scripts["assemble_xfade_hash"],
    }


def _reel_render_input_hash(
    spec: dict,
    scripts: dict,
    stage: str,
    implementation_hash: str,
) -> str:
    refs = spec["refs"]
    basis = {
        "task_schema_version": REEL_RENDER_TASK_SCHEMA_VERSION,
        "episode": spec["episode"],
        "theme": spec["theme"],
        "source_content_hash": spec["source_content_hash"],
        "render_settings": spec["render_settings"],
        "script_hashes": _reel_render_script_hashes(scripts, stage),
        "render_child_wrapper_version": REEL_RENDER_CHILD_WRAPPER_VERSION,
        "snapshot_segments": spec["snapshot_segments"],
        "implementation_hash": implementation_hash,
    }
    if stage == "render_sisters":
        basis.update({
            "turnlist_reel": refs["turnlist_reel"]["content_hash"],
            "audio_manifest": refs["audio_manifest"]["content_hash"],
            "peer_beats": refs["peer_beats"]["content_hash"],
            "rig_manifest": refs["rig_manifest"]["content_hash"],
            "sfx_manifest": refs["sfx_manifest"]["content_hash"],
            "peer_sfx_mode": REEL_RENDER_PEER_SFX_MODE,
            "rough_mode": spec["rough_mode"],
        })
    else:
        for field in _reel_render_ref_fields_for_stage(stage):
            basis[field] = refs[field]["content_hash"]
    for field in (
        "workspace_bundle", "host_sfx_manifest",
        "runtime_sfx_sources_lock", "runtime_sfx_outputs_lock",
    ):
        if field in refs:
            basis[field] = refs[field]["content_hash"]
    return input_hash(basis)


def _reel_render_tags(spec: dict, stage: str, role: str) -> list[str]:
    return [
        "project:ai-sister",
        "workflow:reel5",
        f"episode:{spec['episode']}",
        f"stage:{stage}",
        f"role:{role}",
    ]


def _reel_render_base_metadata(
    spec: dict,
    scripts: dict,
    stage: str,
    role: str,
    render_hash: str,
    implementation_hash: str,
    queue_evidence: dict,
) -> dict:
    return {
        "workflow": f"ai_sister.{stage}",
        "stage": stage,
        "role": role,
        "episode": spec["episode"],
        "theme": _sanitize_error_text(spec["theme"], 120),
        "input_hash": render_hash,
        "source_content_hash": _sanitize_error_text(spec["source_content_hash"], 160),
        "implementation_hash": implementation_hash,
        "task_schema_version": REEL_RENDER_TASK_SCHEMA_VERSION,
        "settings": spec["render_settings"],
        "inputs": {key: _reel_ref_hash_view(value) for key, value in spec["refs"].items()},
        "script_hashes": _reel_render_script_hashes(scripts, stage),
        "queue_evidence": queue_evidence,
    }


def _reel_render_child_env(overlay: dict[str, str]) -> dict:
    env = _clean_child_env()
    for key in RENDER_SHELL_ENV_KNOBS:
        env.pop(key, None)
    env.update(overlay)
    return env


def _reel_render_output_name(role: str) -> str:
    return REEL_RENDER_OUTPUT_NAMES[role]


def _reel_render_reusable_outputs(spec: dict, render_hash: str, stage: str, required_roles: tuple[str, ...], optional_roles: tuple[str, ...] = ()) -> list[dict] | None:
    roles = required_roles + optional_roles
    with connect() as conn:
        rows = conn.execute(
            f"""
            select * from artifacts
            where json_extract(metadata_json, '$.workflow') = ?
              and json_extract(metadata_json, '$.episode') = ?
              and json_extract(metadata_json, '$.stage') = ?
              and json_extract(metadata_json, '$.input_hash') = ?
            order by created_at desc
            """,
            (f"ai_sister.{stage}", spec["episode"], stage, render_hash),
        ).fetchall()
        by_role = {}
        for row in rows:
            metadata = json.loads(row["metadata_json"] or "{}")
            role = str(metadata.get("role") or "")
            if role not in roles or role in by_role:
                continue
            path = artifact_abs_path(conn, row)
            if path and path.is_file():
                metadata["idempotent_reuse"] = True
                metadata["reused_from_artifact_id"] = row["id"]
                metadata["reused_from_task_id"] = row["producing_task_id"]
                by_role[role] = {
                    "path": str(path),
                    "name": path.name,
                    "artifact_type": row["artifact_type"],
                    "output_name": _reel_render_output_name(role),
                    "metadata": metadata,
                    "tags": _reel_render_tags(spec, stage, role),
                }
        if all(role in by_role for role in required_roles):
            return [by_role[role] for role in roles if role in by_role]
    return None


def _expected_render_clips_from_turnlist(path: Path, field: str) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        raise ValueError(f"{field} is not valid JSON") from error
    turns = data.get("turns") if isinstance(data, dict) else None
    if not isinstance(turns, list):
        raise ValueError(f"{field}.turns must be an array")
    clips = []
    seen = set()
    clean_turns = []
    for pos, turn in enumerate(turns):
        if not isinstance(turn, dict) or not isinstance(turn.get("idx"), int):
            raise ValueError(f"{field}.turns[{pos}] has invalid idx")
        clean_turns.append((pos, turn))
    for pos, turn in sorted(clean_turns, key=lambda item: item[1]["idx"]):
        idx = turn["idx"]
        speaker = str(turn.get("speaker") or "").strip()
        if idx < 0 or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", speaker):
            raise ValueError(f"{field}.turns[{pos}] has invalid idx/speaker")
        basename = f"reel_hl{idx:02d}_{speaker}_av.mp4"
        if basename in seen:
            raise ValueError(f"{field} has duplicate expected clip: {basename}")
        seen.add(basename)
        clips.append({
            "basename": basename,
            "idx": idx,
            "speaker": speaker,
        })
    if not clips:
        raise ValueError(f"{field}.turns must not be empty")
    return clips


def _expected_render_clips(spec: dict, stage: str) -> list[dict]:
    field = "turnlist_reel" if stage == "render_sisters" else "turnlist_host"
    return _expected_render_clips_from_turnlist(spec["refs"][field]["path"], field)


def _require_render_output_manifest(
    path: Path,
    field: str,
    *,
    stage: str,
    episode: str,
    source_root: Path,
    reel_subdir: str,
    expected_clips: list[dict],
) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        raise ValueError(f"{field} manifest is not valid JSON") from error
    if not isinstance(raw, dict):
        raise ValueError(f"{field} manifest must be a JSON object")
    allowed_top = {"schema_version", "stage", "role", "episode", "input_hash", "clips"}
    unknown_top = sorted(key for key in raw if key not in allowed_top)
    if unknown_top:
        raise ValueError(f"{field} manifest unknown keys: {', '.join(unknown_top)}")
    if raw.get("schema_version") != "reel-render-output-manifest-v1":
        raise ValueError(f"{field} unknown manifest schema_version")
    if raw.get("stage") != stage:
        raise ValueError(f"{field} manifest stage must be {stage}")
    if str(raw.get("episode") or "").strip() != episode:
        raise ValueError(f"{field} manifest episode must match assemble episode")
    clips = raw.get("clips")
    if not isinstance(clips, list) or not clips:
        raise ValueError(f"{field} manifest clips must be a non-empty array")
    reel = (source_root / reel_subdir).resolve()
    try:
        reel.relative_to(source_root.resolve())
    except ValueError as error:
        raise ValueError(f"{field} reel directory escapes source root") from error
    clean_clips = []
    seen = set()
    mismatched = []
    for pos, clip in enumerate(clips):
        if not isinstance(clip, dict):
            raise ValueError(f"{field} manifest clip {pos} must be an object")
        allowed_clip = {"basename", "idx", "speaker", "sha256", "size"}
        unknown_clip = sorted(key for key in clip if key not in allowed_clip)
        if unknown_clip:
            raise ValueError(f"{field} manifest clip {pos} unknown keys: {', '.join(unknown_clip)}")
        basename = str(clip.get("basename") or "").strip()
        basename_path = Path(basename)
        idx = clip.get("idx")
        speaker = str(clip.get("speaker") or "").strip()
        sha = str(clip.get("sha256") or "").strip()
        size = clip.get("size")
        if basename_path.is_absolute() or ".." in basename_path.parts or len(basename_path.parts) != 1:
            raise ValueError(f"{field} manifest clip {pos} basename must be a single path component")
        if not isinstance(idx, int) or idx < 0 or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", speaker):
            raise ValueError(f"{field} manifest clip {pos} has invalid idx/speaker")
        expected_basename = f"reel_hl{idx:02d}_{speaker}_av.mp4"
        if basename != expected_basename:
            raise ValueError(f"{field} manifest clip {pos} basename does not match idx/speaker")
        if not re.fullmatch(r"[0-9a-f]{64}", sha) or not isinstance(size, int) or size < 0:
            raise ValueError(f"{field} manifest clip {pos} has invalid sha256/size")
        if basename in seen:
            raise ValueError(f"{field} manifest duplicate clip: {basename}")
        seen.add(basename)
        clip_path = (reel / basename).resolve()
        try:
            clip_path.relative_to(reel)
        except ValueError as error:
            raise ValueError(f"{field} manifest clip escapes reel directory: {basename}") from error
        if not clip_path.is_file():
            mismatched.append(basename)
        else:
            actual_sha = _file_sha256(clip_path)
            actual_size = clip_path.stat().st_size
            if actual_sha != sha or actual_size != size:
                mismatched.append(basename)
        clean_clips.append({
            "basename": basename,
            "idx": idx,
            "speaker": speaker,
            "sha256": sha,
            "size": size,
        })
    if mismatched:
        names = ", ".join(Path(name).name for name in mismatched[:5])
        raise ValueError(f"{field} manifest clip files missing or changed: {names}")
    expected_names = {clip["basename"] for clip in expected_clips}
    declared_names = {clip["basename"] for clip in clean_clips}
    if declared_names != expected_names:
        differing = sorted(expected_names ^ declared_names)
        names = ", ".join(Path(name).name for name in differing[:5])
        raise ValueError(f"{field} manifest clips do not match assemble turnlist: {names}")
    return {
        "schema_version": "reel-render-output-manifest-v1",
        "stage": stage,
        "role": str(raw.get("role") or "").strip(),
        "episode": episode,
        "input_hash": str(raw.get("input_hash") or "").strip(),
        "clips": clean_clips,
    }


def _require_render_formula_matches_manifest(path: Path, field: str, *, stage: str, manifest: dict) -> None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        raise ValueError(f"{field} formula is not valid JSON") from error
    if not isinstance(raw, dict):
        raise ValueError(f"{field} formula must be a JSON object")
    if raw.get("schema_version") != "reel-render-formula-v1":
        raise ValueError(f"{field} formula schema_version must be reel-render-formula-v1")
    if raw.get("stage") != stage:
        raise ValueError(f"{field} formula stage must be {stage}")
    formula_hash = str(raw.get("input_hash") or "").strip()
    if not formula_hash:
        raise ValueError(f"{field} formula input_hash is required")
    if formula_hash != manifest["input_hash"]:
        raise ValueError(f"{field} formula input_hash does not match render manifest")


def _collect_expected_render_clips(source_root: Path, reel_subdir: str, expected: list[dict], stage: str) -> list[dict]:
    reel = (source_root / reel_subdir).resolve()
    try:
        reel.relative_to(source_root.resolve())
    except ValueError as error:
        raise RuntimeError(f"failure_kind=missing_input: {stage} reel directory escapes source root") from error
    clips = []
    missing = []
    for expected_clip in expected:
        path = reel / expected_clip["basename"]
        if not path.is_file():
            missing.append(expected_clip["basename"])
            continue
        clips.append({
            **expected_clip,
            "sha256": _file_sha256(path),
            "size": path.stat().st_size,
        })
    if missing:
        names = ", ".join(Path(name).name for name in missing[:5])
        raise RuntimeError(f"failure_kind=missing_input: {stage} missing expected clips: {names}")
    return clips


def _write_render_formula(path: Path, *, stage: str, render_hash: str) -> None:
    _write_text(path, json.dumps({
        "schema_version": "reel-render-formula-v1",
        "stage": stage,
        "input_hash": render_hash,
        "task_schema_version": REEL_RENDER_TASK_SCHEMA_VERSION,
    }, sort_keys=True, indent=2) + "\n")


def _write_render_manifest(path: Path, *, spec: dict, stage: str, role: str, render_hash: str, clips: list[dict]) -> None:
    _write_text(path, json.dumps({
        "schema_version": "reel-render-output-manifest-v1",
        "stage": stage,
        "role": role,
        "episode": spec["episode"],
        "input_hash": render_hash,
        "clips": clips,
    }, sort_keys=True, indent=2) + "\n")


def _run_render_with_classification(payload: dict, cmd: list[str], cwd: Path, cancel_file: Path, *, env: dict, stage: str, render_hash: str) -> dict:
    try:
        _run_cpu_child(cmd, cwd, cancel_file, env=env, error_label=stage)
        return {
            "resource_class": "cpu",
            "executor": "chromium_libx264",
            "gpu_queue_cmd_used": False,
            "gpu_queue_marker_observed": False,
            "gpu_queue_marker_count": 0,
        }
    except RuntimeError as error:
        output = getattr(error, "child_output", str(error))
        kind = _reel_render_failure_kind(output)
        _record_task_event(
            payload,
            "task.render_failure_classified",
            f"Render failed: {kind}",
            {"stage": stage, "episode": str(payload.get("episode") or ""), "input_hash": render_hash, "failure_kind": kind},
            level="warning",
        )
        public = RuntimeError(f"failure_kind={kind}: {_sanitize_error_tail(error)}")
        public.child_output = output
        public.failure_kind = kind
        raise public from error


def _reel_champion_authority_paths(
    source_root: Path,
    episode: str,
    manifest_path: Path,
) -> list[str]:
    canonical = source_root / f"champion_{episode}.json"
    if manifest_path.resolve() != canonical.resolve():
        raise RuntimeError("render_host champion authority is not at its canonical path")
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise RuntimeError("render_host champion authority is missing")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as error:
        raise RuntimeError("render_host champion authority is invalid JSON") from error
    assets = manifest.get("assets") if isinstance(manifest, dict) else None
    generation_relative = _safe_bundle_relative(
        manifest.get("generation") if isinstance(manifest, dict) else None,
        "champion generation",
    )
    generation = (source_root / generation_relative).resolve()
    expected_generation_root = (source_root / f"audio_champion_{episode}" / ".generations").resolve()
    try:
        generation.relative_to(expected_generation_root)
    except ValueError as error:
        raise RuntimeError("champion generation escapes its per-episode authority root") from error
    if (
        manifest.get("schema") != "champion_audio_authority_v1"
        or str(manifest.get("debate_id") or "") != episode
        or not isinstance(assets, list)
        or not assets
    ):
        raise RuntimeError("render_host champion authority identity is invalid")
    paths = [manifest_path.relative_to(source_root).as_posix()]
    seen = set()
    for index, row in enumerate(assets):
        if not isinstance(row, dict):
            raise RuntimeError("champion authority asset row is malformed")
        relative = _safe_bundle_relative(row.get("audio"), f"champion asset {index}")
        if relative in seen:
            raise RuntimeError("champion authority has duplicate audio assets")
        seen.add(relative)
        audio = (source_root / relative).resolve()
        try:
            audio.relative_to(generation)
        except ValueError as error:
            raise RuntimeError("champion authority audio escapes its generation") from error
        digest = str(row.get("sha256") or "")
        size = row.get("size")
        if (
            audio.is_symlink()
            or not audio.is_file()
            or not isinstance(size, int)
            or audio.stat().st_size != size
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or _file_sha256(audio) != digest
        ):
            raise RuntimeError("champion authority audio bytes are invalid")
        paths.append(relative)
    return sorted(paths)


def _reel_prepared_audio_fingerprints(
    source_root: Path,
    episode: str,
    champion_paths: list[str],
) -> dict[str, tuple[int, str]]:
    paths = list(champion_paths)
    paths.extend((
        f"audio_listener_clips_{episode}.json",
        f"audio_ready_{episode}.json",
    ))
    fingerprints = {}
    for relative in paths:
        relative = _safe_bundle_relative(relative, "prepared audio authority path")
        path = (source_root / relative).resolve()
        try:
            path.relative_to(source_root.resolve())
        except ValueError as error:
            raise RuntimeError("prepared audio authority path escapes source root") from error
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("prepared audio authority bytes are missing or unsafe")
        fingerprints[relative] = (path.stat().st_size, _file_sha256(path))
    return fingerprints


def run_ai_sister_render_sisters(payload: dict, work_dir: Path, cancel_file: Path) -> list[dict]:
    source_root = _configured_reel_source_root()
    episode = _episode_id(payload)
    if "workspace_bundle" in payload:
        _require_reel_snapshot_bundle_artifact(
            payload["workspace_bundle"], "workspace_bundle",
            workflow="castle.reel_prepare_render", role="workspace_bundle",
            expected_episode=episode)
    with _reel_workspace_lock(source_root):
        if "workspace_bundle" in payload:
            with _reel_host_audio_authority_lock(
                source_root, episode, exclusive=True,
            ):
                _extract_reel_snapshot_bundle(
                    payload["workspace_bundle"], source_root,
                    "workspace_bundle", expected_episode=episode)
        return _run_ai_sister_render_sisters_locked(payload, work_dir, cancel_file)


def _run_ai_sister_render_sisters_locked(payload: dict, work_dir: Path, cancel_file: Path) -> list[dict]:
    scripts = _resolve_reel_render_script_paths()
    spec = _require_reel_render_payload(payload, scripts, stage="render_sisters")
    actual_implementation_hash = _reel_m4_external_implementation_hash(
        scripts["source_root"], "render_sisters")
    render_hash = _reel_render_input_hash(
        spec, scripts, "render_sisters", actual_implementation_hash)
    required_roles = ("render_sisters", "sisters_formula") + (
        ("sisters_segments",) if spec["snapshot_segments"] else ())
    reusable = (
        _reel_render_reusable_outputs(spec, render_hash, "render_sisters", required_roles)
        if _runner_internal_reuse_allowed(payload) else None
    )
    if reusable:
        _record_task_event(payload, "task.idempotent_reuse", "Reused sister render artifacts", {"episode": spec["episode"], "input_hash": render_hash})
        return reusable

    refs = spec["refs"]
    source_root = scripts["source_root"]
    audio_manifest = spec["manifests"]["audio_manifest"]
    sfx_manifest = spec["manifests"]["sfx_manifest"]
    expected_clips = _expected_render_clips(spec, "render_sisters")
    overlay = {
        "REEL_SUBDIR": spec["reel_subdir"],
        "TL": _relative_to_source(refs["turnlist_reel"]["path"], source_root, "turnlist_reel"),
        "AUD": _manifest_dir(audio_manifest, "spoken"),
        "HL_THEME": spec["theme"],
        "PEER_BEATS": _relative_to_source(refs["peer_beats"]["path"], source_root, "peer_beats"),
        "PUSH_CUE_SFX": _manifest_role_path(sfx_manifest, "push_cue"),
        "BOO_CUE_SFX": _manifest_role_path(sfx_manifest, "boo_cue"),
        "PUSH_POP_SFX": _manifest_role_path(sfx_manifest, "push_pop"),
        "BOO_POP_SFX": _manifest_role_path(sfx_manifest, "boo_pop"),
        "PUSH_BABY_SFX": _manifest_role_path(sfx_manifest, "push_baby"),
        "BOO_BABY_SFX": _manifest_role_path(sfx_manifest, "boo_baby"),
        "RENDER_FPS": str(spec["render_settings"]["render_fps"]),
        "RENDER_CONCURRENCY": str(spec["render_settings"]["render_concurrency"]),
        "VIDEO_PRESET": spec["render_settings"]["video_preset"],
        "VIDEO_CRF": str(spec["render_settings"]["video_crf"]),
        "MUSIC": "0",
        "SKIP_AUDIO": "1",
        "XFADE": str(spec["render_settings"]["xfade"]),
    }
    if set(overlay) != set(RENDER_SISTERS_ENV_OVERLAY_KEYS):
        raise RuntimeError("render_sisters env overlay keys drifted")
    env = _reel_render_child_env(overlay)
    inner = [str(scripts["render_highlight"]), spec["episode"]]
    queue_evidence = _run_render_with_classification(
        payload,
        inner,
        source_root,
        cancel_file,
        env=env,
        stage="render_sisters",
        render_hash=render_hash,
    )
    _record_task_event(payload, "task.cpu_render_observed", "Observed sister render CPU executor", {
        "episode": spec["episode"],
        "input_hash": render_hash,
        "resource_class": queue_evidence["resource_class"],
        "executor": queue_evidence["executor"],
    })
    if _cancelled(cancel_file):
        raise KeyboardInterrupt()
    clips = _collect_expected_render_clips(source_root, spec["reel_subdir"], expected_clips, "render_sisters")
    out = (spec["output_root"] / spec["output_name"]).resolve()
    formula = (spec["output_root"] / f"sisters_formula_{spec['episode']}-{render_hash[:16]}.json").resolve()
    out = _safe_output_path(str(out), "output_name")
    formula = _safe_output_path(str(formula), "sisters_formula")
    _write_render_manifest(out, spec=spec, stage="render_sisters", role="render_sisters", render_hash=render_hash, clips=clips)
    _write_render_formula(formula, stage="render_sisters", render_hash=render_hash)
    base = _reel_render_base_metadata(
        spec, scripts, "render_sisters", "render_sisters",
        render_hash, actual_implementation_hash, queue_evidence)
    manifest_meta = {**base, "clips": clips}
    formula_meta = {**_reel_render_base_metadata(
        spec, scripts, "render_sisters", "sisters_formula",
        render_hash, actual_implementation_hash, queue_evidence),
        "formula_schema_version": "reel-render-formula-v1"}
    outputs = [
        {"path": str(out), "name": out.name, "artifact_type": "json", "output_name": _reel_render_output_name("render_sisters"), "metadata": manifest_meta, "tags": _reel_render_tags(spec, "render_sisters", "render_sisters")},
        {"path": str(formula), "name": formula.name, "artifact_type": "json", "output_name": _reel_render_output_name("sisters_formula"), "metadata": formula_meta, "tags": _reel_render_tags(spec, "render_sisters", "sisters_formula")},
    ]
    if spec["snapshot_segments"]:
        bundle = work_dir / "tmp" / f"render-sisters-{spec['episode']}-{render_hash[:16]}.tar"
        _write_reel_snapshot_bundle(
            source_root,
            [f"{spec['reel_subdir']}/{clip['basename']}" for clip in clips],
            bundle,
            episode=spec["episode"],
            stage="render_sisters",
        )
        bundle_meta = _reel_render_base_metadata(
            spec, scripts, "render_sisters", "sisters_segments",
            render_hash, actual_implementation_hash, queue_evidence)
        outputs.append({
            "path": str(bundle),
            "name": bundle.name,
            "artifact_type": "archive",
            "output_name": _reel_render_output_name("sisters_segments"),
            "metadata": bundle_meta,
            "tags": _reel_render_tags(spec, "render_sisters", "sisters_segments"),
        })
    return _snapshot_output_specs(outputs, work_dir)


def run_ai_sister_render_host(payload: dict, work_dir: Path, cancel_file: Path) -> list[dict]:
    source_root = _configured_reel_source_root()
    episode = _episode_id(payload)
    if "workspace_bundle" in payload:
        _require_reel_snapshot_bundle_artifact(
            payload["workspace_bundle"], "workspace_bundle",
            workflow="castle.reel_prepare_render", role="workspace_bundle",
            expected_episode=episode)
    with _reel_workspace_lock(source_root):
        # Restoring the frozen pointer is a write, so direct readers and the
        # publisher are excluded. The render child becomes the sole shared-lock
        # owner and revalidates this frozen generation under that lock. Keeping
        # a parent SH while the child independently queues another SH can
        # deadlock behind a queued writer (EX).
        if "workspace_bundle" in payload:
            with _reel_host_audio_authority_lock(
                source_root, episode, exclusive=True,
            ):
                _extract_reel_snapshot_bundle(
                    payload["workspace_bundle"], source_root,
                    "workspace_bundle", expected_episode=episode)
        return _run_ai_sister_render_host_locked(
            payload, work_dir, cancel_file)


def _run_ai_sister_render_host_locked(payload: dict, work_dir: Path, cancel_file: Path) -> list[dict]:
    scripts = _resolve_reel_render_script_paths()
    spec = _require_reel_render_payload(payload, scripts, stage="render_host")
    source_root = scripts["source_root"]
    refs = spec["refs"]
    _require_host_audio_render_authority_binding(
        source_root, spec["episode"],
        spec["manifests"]["host_audio_manifest"],
    )
    expected_champion = source_root / f"champion_{spec['episode']}.json"
    expected_audio_ready = source_root / f"audio_ready_{spec['episode']}.json"
    if refs["champion_authority"]["path"].resolve() != expected_champion.resolve():
        raise ValueError("render_host champion_authority is not canonical")
    if refs["audio_ready"]["path"].resolve() != expected_audio_ready.resolve():
        raise ValueError("render_host audio_ready is not canonical")
    champion_paths = _verify_prepared_audio_authorities(
        source_root, spec["episode"], cancel_file)
    prepared_audio_before = _reel_prepared_audio_fingerprints(
        source_root, spec["episode"], champion_paths)
    actual_implementation_hash = _reel_m4_external_implementation_hash(
        scripts["source_root"], "render_host")
    render_hash = _reel_render_input_hash(
        spec, scripts, "render_host", actual_implementation_hash)
    optional_roles = ("host_intro", "host_outro", "host_demo") if spec["adopt_proofs"] else ()
    required_roles = ("render_host", "host_formula") + (
        ("host_segments",) if spec["snapshot_segments"] else ())
    reusable = (
        _reel_render_reusable_outputs(
            spec, render_hash, "render_host",
            required_roles, optional_roles)
        if _runner_internal_reuse_allowed(payload) else None
    )
    if reusable:
        _record_task_event(payload, "task.idempotent_reuse", "Reused host render artifacts", {"episode": spec["episode"], "input_hash": render_hash})
        return reusable

    host_audio_manifest = spec["manifests"]["host_audio_manifest"]
    host_audio_root = f"audio_host_{spec['episode']}"
    expected_clips = _expected_render_clips(spec, "render_host")
    overlay = {
        "REEL_SUBDIR": spec["reel_subdir"],
        "DEBATE_ID": spec["episode"],
        "TURNLIST": _relative_to_source(refs["turnlist_host"]["path"], source_root, "turnlist_host"),
        "TURNLIST_HOST": _relative_to_source(refs["turnlist_host"]["path"], source_root, "turnlist_host"),
        "TURNLIST_REEL": _relative_to_source(refs["turnlist_reel"]["path"], source_root, "turnlist_reel"),
        "REACT_BEATS_REEL": _relative_to_source(refs["peer_beats"]["path"], source_root, "peer_beats"),
        "AUDIO_DIR": _manifest_dir(host_audio_manifest, "intro"),
        "CASTLE_EXPECTED_HOST_AUDIO_GENERATION": _manifest_dir(
            host_audio_manifest, "intro"),
        "CHAMPION_AUDIO_DIR": f"audio_champion_{spec['episode']}",
        "CHAMPION_AUTHORITY_PREPARED": "1",
        "HOST_AUDIO_DIR": host_audio_root,
        "HOST_AUDIO_MANIFEST": f"{host_audio_root}/host_audio_manifest.json",
        "REACT_BEATS": _relative_to_source(refs["host_beats"]["path"], source_root, "host_beats"),
        "CHAMPION_LINES": _relative_to_source(refs["champion_lines"]["path"], source_root, "champion_lines"),
        "CHAMPION_MANIFEST": _relative_to_source(
            refs["champion_authority"]["path"], source_root,
            "champion_authority"),
        "SCENE_THEME": spec["theme"],
        "CHAMP_SEED": spec["render_settings"]["champ_seed"],
        "RENDER_FPS": str(spec["render_settings"]["render_fps"]),
        "RENDER_CONCURRENCY": str(spec["render_settings"]["render_concurrency"]),
        "VIDEO_PRESET": spec["render_settings"]["video_preset"],
        "VIDEO_CRF": str(spec["render_settings"]["video_crf"]),
        "XFADE": str(spec["render_settings"]["xfade"]),
    }
    if "host_sfx_manifest" in spec["manifests"]:
        overlay["POP_SFX"] = _manifest_role_path(
            spec["manifests"]["host_sfx_manifest"], "pop")
    expected_overlay = set(RENDER_HOST_ENV_OVERLAY_KEYS)
    if "host_sfx_manifest" in spec["manifests"]:
        expected_overlay.add("POP_SFX")
    if set(overlay) != expected_overlay:
        raise RuntimeError("render_host env overlay keys drifted")
    env = _reel_render_child_env(overlay)
    inner = [str(scripts["render_host"])]
    queue_evidence = _run_render_with_classification(
        payload,
        inner,
        source_root,
        cancel_file,
        env=env,
        stage="render_host",
        render_hash=render_hash,
    )
    _record_task_event(payload, "task.cpu_render_observed", "Observed host render CPU executor", {
        "episode": spec["episode"],
        "input_hash": render_hash,
        "resource_class": queue_evidence["resource_class"],
        "executor": queue_evidence["executor"],
    })
    if _cancelled(cancel_file):
        raise KeyboardInterrupt()
    prepared_audio_after = _reel_prepared_audio_fingerprints(
        source_root, spec["episode"], champion_paths)
    if prepared_audio_after != prepared_audio_before:
        raise RuntimeError(
            "failure_kind=script_error: render_host mutated prepared audio authority")
    clips = _collect_expected_render_clips(source_root, spec["reel_subdir"], expected_clips, "render_host")
    out = _safe_output_path(str((spec["output_root"] / spec["output_name"]).resolve()), "output_name")
    formula = _safe_output_path(str((spec["output_root"] / f"host_formula_{spec['episode']}-{render_hash[:16]}.json").resolve()), "host_formula")
    _write_render_manifest(out, spec=spec, stage="render_host", role="render_host", render_hash=render_hash, clips=clips)
    _write_render_formula(formula, stage="render_host", render_hash=render_hash)
    outputs = []
    manifest_meta = {**_reel_render_base_metadata(
        spec, scripts, "render_host", "render_host",
        render_hash, actual_implementation_hash, queue_evidence),
        "clips": clips}
    outputs.append({"path": str(out), "name": out.name, "artifact_type": "json", "output_name": _reel_render_output_name("render_host"), "metadata": manifest_meta, "tags": _reel_render_tags(spec, "render_host", "render_host")})
    formula_meta = {**_reel_render_base_metadata(
        spec, scripts, "render_host", "host_formula",
        render_hash, actual_implementation_hash, queue_evidence),
        "formula_schema_version": "reel-render-formula-v1"}
    outputs.append({"path": str(formula), "name": formula.name, "artifact_type": "json", "output_name": _reel_render_output_name("host_formula"), "metadata": formula_meta, "tags": _reel_render_tags(spec, "render_host", "host_formula")})
    if spec["adopt_proofs"]:
        for proof_role, rel in (("host_intro", "host/host_intro.mp4"), ("host_outro", "host/host_outro.mp4"), ("host_demo", "host/host_demo.mp4")):
            proof_src = source_root / rel
            if not proof_src.is_file():
                continue
            proof_out = _safe_output_path(str((spec["output_root"] / f"{proof_role}_{spec['episode']}-{render_hash[:16]}.mp4").resolve()), proof_role)
            _atomic_copy(proof_src, proof_out)
            proof_meta = {**_reel_render_base_metadata(
                spec, scripts, "render_host", proof_role,
                render_hash, actual_implementation_hash, queue_evidence),
                "proof": True, "source_name": proof_src.name}
            outputs.append({"path": str(proof_out), "name": proof_out.name, "artifact_type": "video", "output_name": _reel_render_output_name(proof_role), "metadata": proof_meta, "tags": _reel_render_tags(spec, "render_host", proof_role) + ["proof:true"]})
    if spec["snapshot_segments"]:
        bundle = work_dir / "tmp" / f"render-host-{spec['episode']}-{render_hash[:16]}.tar"
        _write_reel_snapshot_bundle(
            source_root,
            [f"{spec['reel_subdir']}/{clip['basename']}" for clip in clips],
            bundle,
            episode=spec["episode"],
            stage="render_host",
        )
        bundle_meta = _reel_render_base_metadata(
            spec, scripts, "render_host", "host_segments",
            render_hash, actual_implementation_hash, queue_evidence)
        outputs.append({
            "path": str(bundle),
            "name": bundle.name,
            "artifact_type": "archive",
            "output_name": _reel_render_output_name("host_segments"),
            "metadata": bundle_meta,
            "tags": _reel_render_tags(spec, "render_host", "host_segments"),
        })
    return _snapshot_output_specs(outputs, work_dir)


def _validate_reel_qc_rig_handoff(
    refs: dict[str, dict], source_root: Path, episode: str, theme: str,
) -> None:
    if "qc_rig_sources_manifest" not in refs:
        return
    path = refs["qc_rig_sources_manifest"]["path"]
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        raise ValueError("QC rig source manifest is invalid JSON") from error
    rows = manifest.get("sources") if isinstance(manifest, dict) else None
    if (
        manifest.get("schema_version") != "castle-reel-qc-rig-sources-v1"
        or str(manifest.get("episode") or "") != episode
        or manifest.get("theme") != theme
        or not isinstance(rows, list)
        or not rows
    ):
        raise ValueError("QC rig source manifest identity is invalid")
    voice_lab = source_root.parent.resolve()
    seen = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError("QC rig source manifest row is malformed")
        relative = _safe_bundle_relative(
            row.get("path"), f"QC rig source {index}")
        if relative in seen:
            raise ValueError("QC rig source manifest has duplicate paths")
        seen.add(relative)
        source = voice_lab / relative
        digest = str(row.get("sha256") or "")
        size = row.get("size")
        if (
            source.is_symlink()
            or not source.is_file()
            or not isinstance(size, int)
            or source.stat().st_size != size
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or _file_sha256(source) != digest
        ):
            raise ValueError("QC rig source bytes differ from the immutable handoff")


def run_castle_reel_qc(payload: dict, work_dir: Path, cancel_file: Path) -> list[dict]:
    source_root = _configured_reel_source_root()
    episode = _episode_id(payload)
    if "workspace_bundle" in payload:
        _require_reel_snapshot_bundle_artifact(
            payload["workspace_bundle"], "workspace_bundle",
            workflow="castle.reel_prepare_render", role="workspace_bundle",
            expected_episode=episode)
    # Validate every producer before either archive is allowed to write into
    # the compatibility workspace.
    cover_refs = _resolve_reel_qc_cover_refs(payload)
    render_bundle_refs = _resolve_reel_qc_render_bundle_refs(payload)
    rig_refs = _resolve_reel_qc_rig_refs(payload)
    if "cover_bundle" in cover_refs:
        _require_reel_snapshot_bundle_artifact(
            payload["cover_bundle"], "cover_bundle",
            workflow="castle.reel_cover", role="cover_bundle",
            expected_episode=episode)
    if "champion_bundle" in payload:
        _require_reel_snapshot_bundle_artifact(
            payload["champion_bundle"], "champion_bundle",
            workflow="castle.reel_native_stage", role="stage_bundle",
            expected_episode=episode)
    for field, (workflow, role) in REEL_QC_RENDER_BUNDLES.items():
        if field in render_bundle_refs:
            _require_reel_snapshot_bundle_artifact(
                payload[field], field, workflow=workflow, role=role,
                expected_episode=episode)
    if "qc_rig_bundle" in rig_refs:
        _require_reel_snapshot_bundle_artifact(
            payload["qc_rig_bundle"], "qc_rig_bundle",
            workflow="castle.reel_prepare_render", role="qc_rig_bundle",
            expected_episode=episode)
    with _reel_workspace_lock(source_root):
        if "workspace_bundle" in payload:
            with _reel_host_audio_authority_lock(
                source_root, episode, exclusive=True,
            ):
                _extract_reel_snapshot_bundle(
                    payload["workspace_bundle"], source_root,
                    "workspace_bundle", expected_episode=episode)
        if "cover_bundle" in cover_refs:
            _extract_reel_snapshot_bundle(
                payload["cover_bundle"], source_root, "cover_bundle",
                expected_episode=episode)
        if "champion_bundle" in payload:
            _extract_reel_snapshot_bundle(
                payload["champion_bundle"], source_root, "champion_bundle",
                expected_episode=episode)
        for field in REEL_QC_RENDER_BUNDLES:
            if field in render_bundle_refs:
                _extract_reel_snapshot_bundle(
                    payload[field], source_root, field,
                    expected_episode=episode)
        if "qc_rig_bundle" in rig_refs:
            _extract_reel_snapshot_bundle(
                payload["qc_rig_bundle"], source_root.parent, "qc_rig_bundle",
                expected_episode=episode)
            episode = str(payload.get("episode") or "")
            theme = str(payload.get("theme") or "")
            _validate_reel_qc_rig_handoff(
                rig_refs, source_root, episode, theme)
        # qc_reel5.py owns all shared authority locks and validates the frozen
        # generation after acquiring them. Do not nest another SH here: a
        # queued writer between parent/child SH acquisitions creates a cycle.
        return _run_castle_reel_qc_locked(payload, work_dir, cancel_file)


def _run_castle_reel_qc_locked(payload: dict, work_dir: Path, cancel_file: Path) -> list[dict]:
    scripts = _resolve_reel_qc_script_paths()
    spec = _require_reel_qc_payload(payload, scripts)
    inherited_qc_errors = canonical_qc_environment_errors(os.environ)
    if inherited_qc_errors:
        raise ValueError(
            "canonical Reel QC environment rejected: "
            + "; ".join(inherited_qc_errors))
    host_render_manifest = None
    if "workspace_bundle" in spec["refs"]:
        host_render_manifest = _require_manifest_payload(
            spec["refs"]["host_audio_manifest"]["path"],
            "host_audio_manifest",
            source_root=scripts["source_root"],
            required_roles=("intro", "outro"),
        )
        _require_host_audio_render_authority_binding(
            scripts["source_root"], spec["episode"], host_render_manifest)
    actual_implementation_hash = _reel_m4_external_implementation_hash(
        scripts["source_root"], "qc")
    if "cover_bundle" in spec["refs"]:
        canonical_cover = {
            "cover": scripts["source_root"] / f"cover_{spec['episode']}.png",
            "cover_kv": scripts["source_root"] / f"cover_{spec['episode']}_kv.json",
            "cover_authority": (
                scripts["source_root"] / f"cover_authority_{spec['episode']}" / "authority.json"
            ),
        }
        for field, path in canonical_cover.items():
            if (
                path.is_symlink()
                or not path.is_file()
                or _file_sha256(path) != spec["refs"][field]["content_hash"]
            ):
                raise ValueError(f"QC immutable {field} handoff does not match its artifact")
    if "champion_bundle" in spec["refs"]:
        canonical_champion = (
            scripts["source_root"] / f"champion_{spec['episode']}.json"
        )
        if (
            canonical_champion.is_symlink()
            or not canonical_champion.is_file()
            or _file_sha256(canonical_champion)
            != spec["refs"]["champion"]["content_hash"]
        ):
            raise ValueError("QC immutable champion handoff does not match its artifact")
    qc_input_hash = _reel_qc_input_hash(
        spec, scripts, actual_implementation_hash)
    orchestrator_input_hash = task_input_signature("castle.reel_qc", payload)
    reusable = (
        _reel_qc_reusable_output(spec, qc_input_hash, orchestrator_input_hash)
        if _runner_internal_reuse_allowed(payload) else None
    )
    if reusable:
        _record_task_event(
            payload,
            "task.idempotent_reuse",
            "Reused reel QC artifact",
            {"episode": spec["episode"], "input_hash": qc_input_hash},
        )
        print("CASTLE_PROGRESS " + json.dumps({
            "stage": "qc",
            "label": "idempotent reuse",
            "episode": spec["episode"],
            "input_hash": qc_input_hash,
        }, sort_keys=True), flush=True)
        return reusable

    raw_report = (work_dir / "tmp" / "raw_qc_report.json").resolve()
    safe_report = (work_dir / "tmp" / spec["report_name"]).resolve()
    final_report = (spec["output_root"] / spec["report_name"]).resolve()
    final_report = _safe_output_path(str(final_report), "report_name")
    raw_report.parent.mkdir(parents=True, exist_ok=True)
    raw_report.unlink(missing_ok=True)
    inherited_dynamic = {
        "REEL_SUBDIR": spec["reel_subdir"],
        "FINAL_MP4": spec["final_mp4"],
        "REEL_AUD": spec["reel_aud"],
        "HOST_AUD": spec["host_aud"],
        "THEME": spec["theme"],
        "CHAMP_AUD": f"audio_champion_{spec['episode']}",
        "CHAMP_SEED": spec["episode"],
        "COVER_AUTHORITY": f"cover_authority_{spec['episode']}/authority.json",
        "HOST_AUDIO_MANIFEST": (
            f"{spec['host_aud']}/host_audio_manifest.json"
        ),
    }
    for key, expected in inherited_dynamic.items():
        if key in os.environ and str(os.environ[key]) != expected:
            raise ValueError(
                f"QC inherited {key} is noncanonical (required {expected!r})")
    env = _clean_child_env()
    temp_cache = (Path.home() / ".cache/web-comfyui/render-temp" / work_dir.name).resolve()
    frame_cache = (spec["output_root"] / "frames").resolve()
    temp_cache.mkdir(parents=True, exist_ok=True)
    frame_cache.mkdir(parents=True, exist_ok=True)
    env["TMPDIR"] = str(temp_cache)
    env["TEMP"] = str(temp_cache)
    env["TMP"] = str(temp_cache)
    env["QC_FRAME_DIR"] = str(frame_cache)
    if host_render_manifest is not None:
        env["CASTLE_EXPECTED_HOST_AUDIO_GENERATION"] = _manifest_dir(
            host_render_manifest, "intro")
    env["XDG_CACHE_HOME"] = str(
        _reel_runtime_source_roots(scripts["source_root"])["whisper_cache"].parent)
    champion_path = (
        scripts["source_root"] / f"champion_{spec['episode']}.json"
        if "champion_bundle" in spec["refs"]
        else spec["refs"]["champion"]["path"]
    )
    try:
        champion_value = _relative_to_source(champion_path, scripts["source_root"], "champion")
    except ValueError:
        champion_value = str(champion_path)
    if (
        "CHAMPION" in os.environ
        and str(os.environ["CHAMPION"]) != champion_value
    ):
        raise ValueError(
            f"QC inherited CHAMPION is noncanonical (required {champion_value!r})")
    env.update({
        "REEL_SUBDIR": spec["reel_subdir"],
        "CHAMPION": champion_value,
        "FINAL_MP4": spec["final_mp4"],
        "REEL_AUD": spec["reel_aud"],
        "HOST_AUD": spec["host_aud"],
        "THEME": spec["theme"],
    })
    cmd = [str(scripts["shell"]), spec["episode"], "--theme", spec["theme"], "--report", str(raw_report)]
    if spec["skip_vision"]:
        cmd.append("--skip-vision")
    if spec["audio_only"]:
        cmd.append("--audio-only")
    child_error: RuntimeError | None = None
    try:
        _run_reel_qc_child(cmd, scripts["source_root"], cancel_file, env)
    except RuntimeError as error:
        if str(error).startswith("billing_block:"):
            raise
        child_error = error
    if _cancelled(cancel_file):
        raise KeyboardInterrupt()
    if not raw_report.is_file():
        if child_error is not None:
            raise child_error
        raise RuntimeError("reel QC did not create report")
    try:
        raw = json.loads(raw_report.read_text(encoding="utf-8"))
    except Exception as error:
        if child_error is not None:
            raise child_error
        raise RuntimeError("reel QC report is not valid json") from error
    if child_error is not None and (
        getattr(child_error, "returncode", None) != 1
        or str(raw.get("verdict") or "").strip().upper() != "FAIL"
    ):
        raise child_error
    report = _sanitize_reel_qc_report(
        raw, spec, qc_input_hash, orchestrator_input_hash)
    _write_text(safe_report, json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    _atomic_copy(safe_report, final_report)
    metadata = _reel_qc_metadata(
        spec, scripts, qc_input_hash, actual_implementation_hash, report)
    return [{
        "path": str(safe_report),
        "name": final_report.name,
        "artifact_type": "json",
        "output_name": "qc",
        "metadata": metadata,
        "tags": _reel_qc_tags(spec),
    }]


def _validate_reel_cover_wardrobe_receipt(
    source_root: Path,
    spec: dict,
) -> list[dict]:
    receipt_path = source_root / ".castle-native" / spec["episode"] / "wardrobe_gate.json"
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise ValueError("cover wardrobe receipt is missing")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except Exception as error:
        raise ValueError("cover wardrobe receipt is invalid JSON") from error
    selected = receipt.get("selected") if isinstance(receipt, dict) else None
    if (
        receipt.get("schema_version") != "castle-reel-wardrobe-gate-v1"
        or receipt.get("stage") != "wardrobe_gate"
        or str(receipt.get("episode") or "") != spec["episode"]
        or receipt.get("theme") != spec["theme"]
        or not isinstance(selected, list)
        or not selected
    ):
        raise ValueError("cover wardrobe receipt identity is invalid")
    turnlist = source_root / f"turnlist_reel5_{spec['episode']}.json"
    receipt_turnlist_hash = str(receipt.get("turnlist_sha256") or "")
    if (
        turnlist.is_symlink()
        or not turnlist.is_file()
        or not re.fullmatch(r"[0-9a-f]{64}", receipt_turnlist_hash)
        or _file_sha256(turnlist) != receipt_turnlist_hash
        or spec["source_content_hash"] != receipt_turnlist_hash
    ):
        raise ValueError("cover turnlist differs from the wardrobe-gated snapshot")
    references = []
    seen_keys: set[str] = set()
    seen_paths: set[str] = set()
    for index, row in enumerate(selected):
        if not isinstance(row, dict):
            raise ValueError("cover wardrobe receipt row is malformed")
        key = str(row.get("key") or "")
        if (
            not re.fullmatch(r"[a-z0-9_-]{1,80}__[a-z0-9_-]{1,80}", key)
            or not key.endswith(f"__{spec['theme']}")
            or key in seen_keys
        ):
            raise ValueError("cover wardrobe receipt key is invalid")
        approval = row.get("approval")
        if not isinstance(approval, dict) or approval.get("status") != "approved":
            raise ValueError("cover wardrobe approval is no longer approved")
        relative = _safe_bundle_relative(
            row.get("doll_path"), f"cover wardrobe doll {index}")
        if relative in seen_paths:
            raise ValueError("cover wardrobe receipt repeats a doll")
        doll = (source_root / relative).resolve()
        try:
            doll.relative_to(source_root.resolve())
        except ValueError as error:
            raise ValueError("cover wardrobe doll escapes source root") from error
        digest = str(row.get("doll_sha256") or "")
        size = row.get("doll_size")
        if (
            doll.is_symlink()
            or not doll.is_file()
            or isinstance(size, bool)
            or not isinstance(size, int)
            or doll.stat().st_size != size
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or _file_sha256(doll) != digest
        ):
            raise ValueError("cover wardrobe doll differs from the approved snapshot")
        seen_keys.add(key)
        seen_paths.add(relative)
        references.append({
            "key": key,
            "path": relative,
            "bytes": size,
            "sha256": digest,
        })
    return references


def _reel_cover_key_visual_receipt_path(cover_root: Path, episode: str) -> Path:
    return cover_root / f"cover_{episode}_kv.castle-cache.json"


def _reel_cover_key_visual_identity(spec: dict, prompt_sha256: str) -> dict:
    references = spec.get("wardrobe_references")
    if not isinstance(references, list) or not references:
        raise ValueError("cover key-visual cache requires approved wardrobe references")
    if not re.fullmatch(r"[0-9a-f]{64}", str(prompt_sha256 or "")):
        raise ValueError("cover key-visual cache prompt digest is invalid")
    return {
        "schema": REEL_COVER_KEY_VISUAL_CACHE_SCHEMA,
        "art_policy_version": REEL_COVER_ART_POLICY_VERSION,
        "episode": spec["episode"],
        "theme": spec["theme"],
        "topic": spec["topic"],
        "references": references,
        "provider": "openai",
        "model": "gpt-image-2",
        "operation": "images.edit",
        "size": "1024x1536",
        "quality": "high",
        "prompt_sha256": str(prompt_sha256),
    }


def _reel_cover_key_visual_receipt_payload(
    spec: dict,
    source_kv: Path,
    sidecar: dict,
) -> dict:
    if source_kv.is_symlink() or not source_kv.is_file() or source_kv.stat().st_size <= 0:
        raise ValueError("cover key-visual cache PNG is missing")
    references = spec.get("wardrobe_references")
    expected_cast = [
        str(row.get("key") or "").rsplit("__", 1)[0]
        for row in references
    ] if isinstance(references, list) else []
    prompt_sha256 = str(sidecar.get("prompt_sha256") or "")
    sidecar_topic = str(sidecar.get("topic") or "").strip()
    kv_sha256 = _file_sha256(source_kv)
    if (
        sidecar.get("schema") != COVER_METADATA_SCHEMA
        or str(sidecar.get("debate_id") or "") != spec["episode"]
        or sidecar.get("theme") != spec["theme"]
        or not sidecar_topic
        or sidecar.get("mode") != CANONICAL_COVER_MODE
        or sidecar.get("cast") != expected_cast
        or sidecar.get("kv_png_sha256") != kv_sha256
    ):
        raise ValueError("cover key-visual cache metadata identity is invalid")
    identity = _reel_cover_key_visual_identity(
        {**spec, "topic": sidecar_topic}, prompt_sha256)
    return {
        **identity,
        "key_visual_input_hash": input_hash(identity),
        "kv_png_sha256": kv_sha256,
    }


_REEL_COVER_KEY_VISUAL_IDENTITY_FIELDS = frozenset({
    "schema",
    "art_policy_version",
    "episode",
    "theme",
    "topic",
    "references",
    "provider",
    "model",
    "operation",
    "size",
    "quality",
    "prompt_sha256",
})
_REEL_COVER_KEY_VISUAL_RECEIPT_FIELDS = (
    _REEL_COVER_KEY_VISUAL_IDENTITY_FIELDS
    | {"key_visual_input_hash", "kv_png_sha256"}
)
_REEL_COVER_KEY_VISUAL_LEGACY_CONTEXT_FIELDS = frozenset({
    "source_content_hash",
    "wardrobe_bundle_hash",
})


def _normalize_reel_cover_key_visual_receipt(receipt: dict) -> dict | None:
    """Normalize the original v1 receipt without weakening byte identity.

    Early v1 receipts included the complete turnlist and aggregate wardrobe
    bundle hashes in the provider-art identity.  Those hashes remain part of
    the full cover task/audit identity, but neither is an input to
    ``images.edit``.  A new script snapshot therefore caused an unnecessary
    paid regeneration even when the prompt and every approved reference byte
    were unchanged.

    Legacy migration is deliberately exact: no unknown keys are accepted and
    the old derived hash must verify against the complete old identity before
    its two context-only fields are dropped.  Reference, prompt, provider,
    model, policy, and KV byte identities are never normalized away.
    """
    if not isinstance(receipt, dict):
        return None
    keys = set(receipt)
    canonical_keys = set(_REEL_COVER_KEY_VISUAL_RECEIPT_FIELDS)
    legacy_keys = canonical_keys | set(
        _REEL_COVER_KEY_VISUAL_LEGACY_CONTEXT_FIELDS)
    if keys == canonical_keys:
        return dict(receipt)
    if keys != legacy_keys:
        return None

    legacy_identity = {
        key: receipt[key]
        for key in (
            _REEL_COVER_KEY_VISUAL_IDENTITY_FIELDS
            | _REEL_COVER_KEY_VISUAL_LEGACY_CONTEXT_FIELDS
        )
    }
    if receipt.get("key_visual_input_hash") != input_hash(legacy_identity):
        return None
    normalized_identity = {
        key: receipt[key]
        for key in _REEL_COVER_KEY_VISUAL_IDENTITY_FIELDS
    }
    return {
        **normalized_identity,
        "key_visual_input_hash": input_hash(normalized_identity),
        "kv_png_sha256": receipt["kv_png_sha256"],
    }


def _atomic_reel_cover_json(path: Path, value: dict) -> None:
    payload = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.tmp-{os.getpid()}-{time.time_ns()}")
    fd = -1
    try:
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if fd >= 0:
            os.close(fd)
        temporary.unlink(missing_ok=True)


def _write_reel_cover_key_visual_receipt(
    cover_root: Path,
    spec: dict,
    source_kv: Path,
    sidecar: dict,
) -> dict | None:
    if not spec.get("wardrobe_bundle_ref"):
        return None
    receipt = _reel_cover_key_visual_receipt_payload(spec, source_kv, sidecar)
    _atomic_reel_cover_json(
        _reel_cover_key_visual_receipt_path(cover_root, spec["episode"]),
        receipt,
    )
    return receipt


def _reel_cover_key_visual_receipt_matches(
    cover_root: Path,
    spec: dict,
    source_kv: Path,
    source_sidecar: Path,
) -> tuple[bool, dict | None]:
    if not spec.get("wardrobe_bundle_ref"):
        return False, None
    receipt_path = _reel_cover_key_visual_receipt_path(
        cover_root, spec["episode"])
    if (
        receipt_path.is_symlink()
        or not receipt_path.is_file()
        or source_sidecar.is_symlink()
        or not source_sidecar.is_file()
    ):
        return False, None
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        sidecar = json.loads(source_sidecar.read_text(encoding="utf-8"))
        if not isinstance(receipt, dict) or not isinstance(sidecar, dict):
            return False, None
        expected = _reel_cover_key_visual_receipt_payload(
            spec, source_kv, sidecar)
        normalized = _normalize_reel_cover_key_visual_receipt(receipt)
    except (OSError, TypeError, ValueError):
        return False, None
    if normalized != expected:
        return False, None
    if receipt != normalized:
        # Canonicalize a verified legacy v1 receipt under the workspace lock.
        # A failed durable rewrite aborts the task before the provider boundary
        # instead of turning a local migration error into another image bill.
        _atomic_reel_cover_json(receipt_path, normalized)
    return True, normalized


_REEL_COVER_VERIFY_MARKER = "CASTLE_REEL_COVER_VERIFY_JSON "
_REEL_COVER_RUNTIME_MARKER = "CASTLE_REEL_COVER_RUNTIME_JSON "
_REEL_COVER_RUNTIME_SCHEMA = "castle-reel-cover-runtime-v1"
_REEL_COVER_RUNTIME_MAX_OUTPUT = 64 * 1024
_REEL_COVER_VERIFY_PROGRAM = r"""
import importlib.util
import json
import sys
from pathlib import Path

marker = "CASTLE_REEL_COVER_VERIFY_JSON "
module_path = Path(sys.argv[1]).absolute()
root = module_path.parent.resolve()
if module_path.is_symlink() or not module_path.is_file() or module_path.resolve().parent != root:
    raise RuntimeError("canonical cover verifier path is unsafe")
request = json.load(sys.stdin)
if set(request) != {
    "debate_id", "root", "expected_theme", "manifest_path",
    "working_final_path", "working_meta_path",
}:
    raise RuntimeError("canonical cover verifier request shape is invalid")

# This is a fresh isolated interpreter.  Still remove the canonical helper name
# explicitly so a future verifier import refactor cannot inherit a preload.
sys.modules.pop("cover_title_layout", None)
sys.path.insert(0, str(root))
name = "_castle_pinned_cover_authority"
spec = importlib.util.spec_from_file_location(name, module_path)
if spec is None or spec.loader is None:
    raise RuntimeError("canonical cover verifier cannot be loaded")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
verified = module.load_verified_cover_authority(**request)
layout_module = sys.modules.get("cover_title_layout")
if layout_module is not None and Path(layout_module.__file__).resolve().parent != root:
    raise RuntimeError("canonical title-layout verifier escaped configured root")
print(marker + json.dumps({
    "schema": getattr(module, "SCHEMA", None),
    "meta_schema": getattr(module, "META_SCHEMA", None),
    "verified": verified,
}, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
"""

_REEL_COVER_RUNTIME_PROGRAM = r"""
import hashlib
import importlib
import json
import platform
import sys
from pathlib import Path

marker = "CASTLE_REEL_COVER_RUNTIME_JSON "
module_path = Path(sys.argv[1]).absolute()
root = module_path.parent.resolve()
if module_path.is_symlink() or not module_path.is_file() or module_path.resolve().parent != root:
    raise RuntimeError("canonical cover runtime module path is unsafe")

sys.modules.pop("cover_title_layout", None)
sys.path.insert(0, str(root))
module = importlib.import_module("cover_title_layout")
if Path(module.__file__).resolve() != module_path.resolve():
    raise RuntimeError("canonical cover runtime module escaped configured root")

pillow = getattr(module, "PIL", None)
pillow_version = getattr(pillow, "__version__", None)
title_fingerprint = module.title_layout_policy_fingerprint()
composition_fingerprint = module.cover_composition_policy_fingerprint()
executable = Path(sys.executable)
if not executable.is_file():
    raise RuntimeError("canonical cover Python executable is unreadable")
digest = hashlib.sha256()
with executable.open("rb") as handle:
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)

print(marker + json.dumps({
    "schema": "castle-reel-cover-runtime-v1",
    "python_implementation": sys.implementation.name,
    "python_version": platform.python_version(),
    "python_cache_tag": sys.implementation.cache_tag,
    "python_executable_sha256": digest.hexdigest(),
    "pillow_version": pillow_version,
    "title_layout_policy_fingerprint": title_fingerprint,
    "composition_policy_fingerprint": composition_fingerprint,
}, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
"""


def _reel_cover_verifier_env() -> dict[str, str]:
    """Minimal non-secret environment for the read-only authority verifier."""
    allowed = ("HOME", "LANG", "LC_ALL", "PATH", "TZ")
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env.update({
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONUTF8": "1",
    })
    return env


def _reel_cover_python_launcher_record(cover_python: Path) -> dict[str, str]:
    """Bind the configured launcher while preserving venv symlink semantics."""
    if not cover_python.is_file():
        raise ValueError("canonical reel cover Python is missing or unsafe")
    try:
        launcher_sha256 = _file_sha256(cover_python)
    except OSError as error:
        raise ValueError("canonical reel cover Python is unreadable") from error
    return {
        "path": "runtime:cover-python-launcher-v1",
        "sha256": input_hash({
            "configured_path": str(cover_python.absolute()),
            "launcher_sha256": launcher_sha256,
        }),
    }


def _reel_cover_runtime_record(
    cover_root: Path,
    cover_python: Path,
) -> dict[str, str]:
    """Bind cover reuse to its exact Python/Pillow/font composition runtime."""
    module_path = cover_root / "cover_title_layout.py"
    if module_path.is_symlink() or not module_path.is_file():
        raise ValueError("canonical reel cover runtime module is missing or unsafe")
    if not cover_python.is_file():
        raise ValueError("canonical reel cover Python is missing or unsafe")
    try:
        completed = subprocess.run(
            [str(cover_python), "-B", "-I", "-X", "utf8", "-c",
             _REEL_COVER_RUNTIME_PROGRAM, str(module_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(cover_root),
            env=_reel_cover_verifier_env(),
            timeout=10,
            check=False,
        )
    except (OSError, UnicodeError, subprocess.TimeoutExpired) as error:
        raise ValueError("canonical reel cover runtime probe could not run") from error
    if (
        completed.returncode != 0
        or len(completed.stdout.encode("utf-8")) > _REEL_COVER_RUNTIME_MAX_OUTPUT
        or len(completed.stderr.encode("utf-8")) > _REEL_COVER_RUNTIME_MAX_OUTPUT
    ):
        raise ValueError("canonical reel cover runtime probe failed")
    marker_lines = [
        line[len(_REEL_COVER_RUNTIME_MARKER):]
        for line in completed.stdout.splitlines()
        if line.startswith(_REEL_COVER_RUNTIME_MARKER)
    ]
    if len(marker_lines) != 1:
        raise ValueError("canonical reel cover runtime probe response is missing")
    try:
        identity = json.loads(marker_lines[0])
    except (TypeError, ValueError) as error:
        raise ValueError(
            "canonical reel cover runtime probe response is invalid") from error
    expected_keys = {
        "schema",
        "python_implementation",
        "python_version",
        "python_cache_tag",
        "python_executable_sha256",
        "pillow_version",
        "title_layout_policy_fingerprint",
        "composition_policy_fingerprint",
    }
    safe_token = re.compile(r"[A-Za-z0-9._+-]{1,120}")
    if (
        not isinstance(identity, dict)
        or set(identity) != expected_keys
        or identity.get("schema") != _REEL_COVER_RUNTIME_SCHEMA
        or any(
            not isinstance(identity.get(key), str)
            or safe_token.fullmatch(identity[key]) is None
            for key in (
                "python_implementation", "python_version",
                "python_cache_tag", "pillow_version",
            )
        )
        or any(
            not isinstance(identity.get(key), str)
            or re.fullmatch(r"[0-9a-f]{64}", identity[key]) is None
            for key in (
                "python_executable_sha256",
                "title_layout_policy_fingerprint",
                "composition_policy_fingerprint",
            )
        )
    ):
        raise ValueError("canonical reel cover runtime identity is malformed")
    return {
        "path": "runtime:cover-python-composition-v1",
        "sha256": input_hash(identity),
    }


def _verify_reel_cover_authority_pinned(
    cover_root: Path,
    cover_python: Path,
    *,
    debate_id: str,
    expected_theme: str,
    manifest_path: Path,
    working_final_path: Path,
    working_meta_path: Path,
) -> dict:
    """Verify with the exact runtime that generated the canonical cover.

    Cover composition evidence deliberately binds the Pillow version.  Loading
    voice-lab source into Castle's system Python therefore is not equivalent to
    running the pinned voice-lab verifier and caused v37's false quarantine.
    """
    module_path = cover_root / "cover_authority.py"
    if module_path.is_symlink() or not module_path.is_file():
        raise RuntimeError("canonical reel cover authority verifier is missing")
    request = {
        "debate_id": debate_id,
        "root": str(cover_root),
        "expected_theme": expected_theme,
        "manifest_path": str(manifest_path),
        "working_final_path": str(working_final_path),
        "working_meta_path": str(working_meta_path),
    }
    try:
        completed = subprocess.run(
            [str(cover_python), "-I", "-c", _REEL_COVER_VERIFY_PROGRAM,
             str(module_path)],
            input=json.dumps(request, ensure_ascii=False, sort_keys=True),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(cover_root),
            env=_reel_cover_verifier_env(),
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("canonical reel cover authority verifier could not run") from error
    if completed.returncode != 0:
        detail = _sanitize_error_tail(completed.stderr or completed.stdout)
        raise RuntimeError(
            "canonical reel cover authority verifier rejected output"
            + (f": {detail}" if detail else ""))
    marker_lines = [
        line[len(_REEL_COVER_VERIFY_MARKER):]
        for line in completed.stdout.splitlines()
        if line.startswith(_REEL_COVER_VERIFY_MARKER)
    ]
    if len(marker_lines) != 1:
        raise RuntimeError("canonical reel cover authority verifier response is missing")
    try:
        response = json.loads(marker_lines[0])
    except (TypeError, ValueError) as error:
        raise RuntimeError("canonical reel cover authority verifier response is invalid") from error
    if (
        not isinstance(response, dict)
        or response.get("schema") != COVER_AUTHORITY_SCHEMA
        or response.get("meta_schema") != COVER_METADATA_SCHEMA
        or not isinstance(response.get("verified"), dict)
    ):
        raise RuntimeError("canonical reel cover verifier schema is unsupported")
    return response["verified"]


def _reel_cover_authority_handoff(
    cover_root: Path,
    cover_python: Path,
    spec: dict,
    source_image: Path,
    source_sidecar: Path,
    sidecar: dict,
) -> tuple[Path, list[str], dict]:
    """Validate and enumerate the complete immutable cover authority closure."""
    episode = spec["episode"]
    authority_root = cover_root / f"cover_authority_{episode}"
    authority = authority_root / "authority.json"
    if authority.is_symlink() or not authority.is_file():
        raise RuntimeError("reel cover generator did not publish cover authority")
    if source_image.is_symlink() or source_sidecar.is_symlink():
        raise RuntimeError("working reel cover authority uses a symlink")
    try:
        payload = json.loads(authority.read_text(encoding="utf-8"))
    except Exception as error:
        raise RuntimeError("reel cover authority is not valid json") from error
    if not isinstance(payload, dict):
        raise RuntimeError("reel cover authority is not a JSON object")
    try:
        verified = _verify_reel_cover_authority_pinned(
            cover_root,
            cover_python,
            debate_id=episode,
            expected_theme=spec["theme"],
            manifest_path=authority,
            working_final_path=source_image,
            working_meta_path=source_sidecar,
        )
    except RuntimeError:
        raise
    except Exception as error:
        raise RuntimeError(
            f"canonical reel cover authority rejected output: {error}") from error

    if (
        payload.get("schema") != COVER_AUTHORITY_SCHEMA
        or str(payload.get("debate_id") or "") != episode
        or payload.get("theme") != spec["theme"]
        or payload.get("mode") != CANONICAL_COVER_MODE
        or verified.get("mode") != CANONICAL_COVER_MODE
    ):
        raise RuntimeError("reel cover authority identity/mode does not match the task")
    sidecar_topic = str(sidecar.get("topic") or "").strip() if isinstance(sidecar, dict) else ""
    if (
        not isinstance(sidecar, dict)
        or sidecar.get("schema") != COVER_METADATA_SCHEMA
        or str(sidecar.get("debate_id") or "") != episode
        or sidecar.get("theme") != spec["theme"]
        or not sidecar_topic
        or sidecar.get("mode") != CANONICAL_COVER_MODE
        or sidecar.get("prompt_sha256") != verified.get("prompt_sha256")
    ):
        raise RuntimeError("reel cover metadata identity/composition is noncanonical")

    paths = [
        source_image.relative_to(cover_root).as_posix(),
        source_sidecar.relative_to(cover_root).as_posix(),
        authority.relative_to(cover_root).as_posix(),
    ]
    verified_digests = {}
    for role in ("key_visual", "metadata", "final_png"):
        record = payload.get(role)
        if not isinstance(record, dict):
            raise RuntimeError(f"reel cover authority is missing {role}")
        relative = _safe_bundle_relative(record.get("path"), f"cover authority {role}")
        candidate = (authority_root / relative).resolve()
        try:
            candidate.relative_to(authority_root.resolve())
        except ValueError as error:
            raise RuntimeError("reel cover authority object escapes its root") from error
        digest = str(record.get("sha256") or "")
        size = record.get("bytes")
        if (
            candidate.is_symlink()
            or not candidate.is_file()
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or not isinstance(size, int)
            or size != candidate.stat().st_size
            or digest != _file_sha256(candidate)
        ):
            raise RuntimeError(f"reel cover authority {role} bytes are invalid")
        canonical_record = verified.get(role)
        if (
            not isinstance(canonical_record, dict)
            or canonical_record.get("sha256") != digest
            or canonical_record.get("bytes") != size
            or Path(str(canonical_record.get("path") or "")).resolve() != candidate
        ):
            raise RuntimeError(f"canonical reel cover verifier disagrees on {role}")
        verified_digests[role] = digest
        paths.append(candidate.relative_to(cover_root).as_posix())
    if verified_digests["final_png"] != _file_sha256(source_image):
        raise RuntimeError("working cover differs from immutable authority")
    if verified_digests["metadata"] != _file_sha256(source_sidecar):
        raise RuntimeError("working cover metadata differs from immutable authority")
    audit = build_cover_audit(
        authority={
            "schema": payload["schema"],
            "authority_sha256": _file_sha256(authority),
            "metadata_sha256": verified_digests["metadata"],
            "key_visual_sha256": verified_digests["key_visual"],
            "final_png_sha256": verified_digests["final_png"],
        },
        metadata=sidecar,
    )
    validate_cover_audit(
        audit,
        expected_final_sha256=_file_sha256(source_image),
        expected_metadata_sha256=_file_sha256(source_sidecar),
        expected_authority_sha256=_file_sha256(authority),
    )
    return authority, sorted(set(paths)), audit


def run_castle_reel_cover(payload: dict, work_dir: Path, cancel_file: Path) -> list[dict]:
    source_root = _configured_reel_source_root()
    spec = _require_reel_cover_payload(payload)
    spec["wardrobe_references"] = []
    if spec["wardrobe_bundle_ref"] is not None:
        _require_reel_snapshot_bundle_artifact(
            spec["wardrobe_bundle"],
            "wardrobe_bundle",
            workflow="castle.reel_native_stage",
            role="stage_bundle",
            expected_episode=spec["episode"],
        )
    with _reel_workspace_lock(source_root):
        if spec["wardrobe_bundle_ref"] is not None:
            _extract_reel_snapshot_bundle(
                spec["wardrobe_bundle"], source_root, "wardrobe_bundle",
                expected_episode=spec["episode"])
            spec["wardrobe_references"] = (
                _validate_reel_cover_wardrobe_receipt(source_root, spec))
        outputs = _run_castle_reel_cover_locked(
            payload, work_dir, cancel_file, spec=spec)
        return _snapshot_output_specs(outputs, work_dir)


def _run_castle_reel_cover_locked(
    payload: dict,
    work_dir: Path,
    cancel_file: Path,
    *,
    spec: dict | None = None,
) -> list[dict]:
    spec = spec or _require_reel_cover_payload(payload)
    actual_implementation_hash = _reel_m4_external_implementation_hash(
        _configured_reel_source_root(), "cover")
    cover_input_hash = _reel_cover_input_hash(
        spec, actual_implementation_hash)
    reusable = (
        _reel_cover_reusable_outputs(spec, cover_input_hash)
        if _runner_internal_reuse_allowed(payload) else None
    )
    if reusable:
        _record_task_event(
            payload,
            "task.idempotent_reuse",
            "Reused reel cover artifacts",
            {
                "episode": spec["episode"],
                "input_hash": cover_input_hash,
            },
        )
        print("CASTLE_PROGRESS " + json.dumps({
            "stage": "cover",
            "label": "idempotent reuse",
            "episode": spec["episode"],
            "input_hash": cover_input_hash,
        }, sort_keys=True), flush=True)
        return reusable

    try:
        cover_python = _trusted_existing_path("CASTLE_REEL_COVER_PYTHON", Path.home() / ".local/share/web-comfyui/integrations/voice/.venv/bin/python", "cover_python")
    except ValueError as error:
        raise ValueError("cover_python not configured; check CASTLE_REEL_COVER_PYTHON") from error
    try:
        worker = _trusted_existing_path("CASTLE_REEL_COVER_SCRIPT", Path.home() / ".local/share/web-comfyui/integrations/voice/video-mvp/gen_cover5.py", "cover_script")
    except ValueError as error:
        raise ValueError("cover_script not configured; check CASTLE_REEL_COVER_SCRIPT") from error
    if spec["wardrobe_bundle_ref"] is not None and worker.parent.resolve() != _configured_reel_source_root():
        raise ValueError("immutable wardrobe cover worker must live in the Reel source root")
    output_dir = (spec["output_root"] / f"reel{spec['episode']}" / "cover").resolve()
    output = output_dir / f"cover_{spec['episode']}-{cover_input_hash[:16]}.png"
    sidecar = output_dir / f"cover_{spec['episode']}-{cover_input_hash[:16]}.json"
    source_image = worker.parent / f"cover_{spec['episode']}.png"
    source_kv = worker.parent / f"cover_{spec['episode']}_kv.png"
    source_sidecar = worker.parent / f"cover_{spec['episode']}_kv.json"
    # Do not unlink cover_<episode>* here: the kv sidecar is gen_cover5's no-re-spend cache, and shell owns these names until Phase 2b.
    def load_generated_sidecar() -> dict:
        if source_image.is_symlink() or not source_image.is_file():
            raise RuntimeError("reel cover generator did not create cover image")
        if source_sidecar.is_symlink() or not source_sidecar.is_file():
            raise RuntimeError("reel cover generator did not create metadata sidecar")
        try:
            value = json.loads(source_sidecar.read_text(encoding="utf-8"))
        except Exception as error:
            raise RuntimeError(
                "reel cover generator metadata sidecar is not valid json") from error
        if not isinstance(value, dict):
            raise RuntimeError("reel cover generator metadata sidecar must be an object")
        return value

    raw_sidecar = None
    authority = None
    handoff_paths = None
    cover_audit = None
    key_visual_receipt = None
    retry_token = str(payload.get(REEL_COVER_POSTGEN_RETRY_TOKEN) or "")
    retry_matches = bool(
        retry_token
        and retry_token == task_input_signature("castle.reel_cover", payload)
    )
    if retry_matches:
        try:
            candidate_sidecar = load_generated_sidecar()
            candidate_handoff = _reel_cover_authority_handoff(
                worker.parent.resolve(), cover_python, spec,
                source_image, source_sidecar, candidate_sidecar)
            candidate_receipt = _write_reel_cover_key_visual_receipt(
                worker.parent.resolve(), spec, source_kv, candidate_sidecar)
        except (OSError, TypeError, ValueError, RuntimeError) as error:
            _record_task_event(
                payload,
                "task.cover_postgen_recovery_miss",
                "Existing post-generation cover could not be recovered",
                {"episode": spec["episode"], "reason": _sanitize_error_tail(error)},
                level="warning",
            )
        else:
            raw_sidecar = candidate_sidecar
            authority, handoff_paths, cover_audit = candidate_handoff
            key_visual_receipt = candidate_receipt
            _record_task_event(
                payload,
                "task.cover_postgen_recovered",
                "Recovered verified cover without another image generation",
                {"episode": spec["episode"], "input_hash": cover_input_hash},
            )
            print("CASTLE_PROGRESS " + json.dumps({
                "stage": "cover",
                "label": "post-generation recovery",
                "episode": spec["episode"],
                "input_hash": cover_input_hash,
            }, sort_keys=True), flush=True)

    if authority is None:
        cache_hit, key_visual_receipt = _reel_cover_key_visual_receipt_matches(
            worker.parent.resolve(), spec, source_kv, source_sidecar)
        cmd = [
            str(cover_python), str(worker), spec["episode"],
            "--theme", spec["theme"],
        ]
        if spec["wardrobe_bundle_hash"] and not cache_hit:
            # The voice worker's prompt/KV digest cache handles prompt-policy
            # invalidation.  Castle additionally requires this exact approved
            # wardrobe receipt before a task cache miss may reuse provider art.
            cmd.append("--regen-art")
        _record_task_event(
            payload,
            "task.cover_key_visual_cache_hit" if cache_hit
            else "task.cover_key_visual_cache_miss",
            "Reused exact key-visual cache" if cache_hit
            else "Key-visual cache requires provider regeneration",
            {"episode": spec["episode"]},
        )
        child_env = dict(os.environ)
        child_env["CASTLE_EXPECTED_COVER_TOPIC"] = spec["topic"]
        _run_reel_cover_child(cmd, worker.parent, cancel_file, env=child_env)
        if _cancelled(cancel_file):
            raise KeyboardInterrupt()
        raw_sidecar = load_generated_sidecar()
        # Commit art-cache identity before the release handoff.  A verifier/QC
        # failure can then retry the same provider result without another bill.
        key_visual_receipt = _write_reel_cover_key_visual_receipt(
            worker.parent.resolve(), spec, source_kv, raw_sidecar)
        authority, handoff_paths, cover_audit = _reel_cover_authority_handoff(
            worker.parent.resolve(), cover_python, spec,
            source_image, source_sidecar, raw_sidecar)

    assert raw_sidecar is not None
    assert authority is not None and handoff_paths is not None
    assert cover_audit is not None
    metadata = _reel_cover_metadata(
        spec, cover_input_hash, actual_implementation_hash, source_image,
        raw_sidecar, cover_audit, key_visual_receipt)
    tmp_sidecar = work_dir / "tmp" / sidecar.name
    _write_text(tmp_sidecar, json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    _atomic_copy(source_image, output)
    _atomic_copy(tmp_sidecar, sidecar)
    tags = _reel_cover_tags(spec)
    outputs = [
        {
            "path": str(output),
            "name": output.name,
            "artifact_type": "image",
            "output_name": "cover",
            "metadata": metadata,
            "tags": tags,
        },
        {
            "path": str(sidecar),
            "name": sidecar.name,
            "artifact_type": "json",
            "output_name": "metadata",
            "metadata": metadata,
            "tags": _reel_cover_role_tags(spec, "cover_metadata"),
        },
    ]
    handoff = work_dir / "tmp" / f"cover-{spec['episode']}-{cover_input_hash[:16]}.castle-reel.tar"
    manifest = _write_reel_snapshot_bundle(
        worker.parent.resolve(),
        handoff_paths,
        handoff,
        episode=spec["episode"],
        stage="cover",
    )
    handoff_metadata = {
        **metadata,
        "role": "cover_bundle",
        "snapshot_schema_version": REEL_NATIVE_BUNDLE_SCHEMA_VERSION,
        "snapshot_file_count": len(manifest["files"]),
    }
    outputs.extend([
        {
            "path": str(source_sidecar),
            "name": source_sidecar.name,
            "artifact_type": "json",
            "output_name": "cover_kv",
            "metadata": {**metadata, "role": "cover_kv"},
            "tags": _reel_cover_role_tags(spec, "cover_kv"),
        },
        {
            "path": str(authority),
            "name": authority.name,
            "artifact_type": "json",
            "output_name": "cover_authority",
            "metadata": {**metadata, "role": "cover_authority"},
            "tags": _reel_cover_role_tags(spec, "cover_authority"),
        },
        {
            "path": str(handoff),
            "name": handoff.name,
            "artifact_type": "archive",
            "output_name": "cover_bundle",
            "metadata": handoff_metadata,
            "tags": _reel_cover_role_tags(spec, "cover_bundle"),
        },
    ])
    return outputs


def run_castle_voxcpm_tts(payload: dict, work_dir: Path, cancel_file: Path) -> list[dict]:
    voxcpm_python = _trusted_existing_path("CASTLE_VOXCPM_PYTHON", Path.home() / ".local/share/web-comfyui/integrations/voice/voxcpm/.venv/bin/python", "voxcpm_python")
    worker = _trusted_existing_path("CASTLE_VOXCPM_SCRIPT", WEBUI_ROOT / "voxcpm_tts.py", "worker_script")
    model_path = _trusted_existing_path("CASTLE_VOXCPM_MODEL_PATH", Path.home() / ".local/share/web-comfyui/integrations/voice/voxcpm/models/VoxCPM2", "model_path", file=False)
    output = _safe_output_path(payload.get("output_path"), "output_path")
    sidecar = _safe_output_path(payload.get("sidecar_path"), "sidecar_path")
    tmp_output = work_dir / "tmp" / output.name
    tmp_sidecar = work_dir / "tmp" / sidecar.name
    seed = payload.get("seed")
    inner = [
        str(voxcpm_python),
        str(worker),
        "--mode", str(payload.get("mode") or "lora"),
        "--text", str(payload.get("text") or ""),
        "--output", str(tmp_output),
        "--sidecar", str(tmp_sidecar),
        "--job-id", str(payload.get("_run_id") or payload.get("_task_id") or "orchestrator"),
        "--workflow", str(payload.get("workflow") or "castle-voxcpm-tts-web"),
        "--model-path", str(model_path),
        "--language", str(payload.get("language") or ""),
        "--accent", str(payload.get("accent") or ""),
        "--profile", str(payload.get("profile") or ""),
        "--control", str(payload.get("control") or ""),
        "--cfg", str(payload.get("cfg") or 2.0),
        "--steps", str(payload.get("steps") or 10),
        "--cancel-file", str(cancel_file),
    ]
    if seed not in (None, "", -1, "-1", "None", "none"):
        inner += ["--seed", str(seed)]
    if payload.get("reference_audio"):
        inner += ["--reference-audio", str(_safe_existing_input_path(payload.get("reference_audio"), "reference_audio"))]
    if payload.get("prompt_text"):
        inner += ["--prompt-text", str(payload.get("prompt_text") or "")]
    if payload.get("lora_path"):
        inner += ["--lora-path", str(_safe_existing_input_dir_or_file(payload.get("lora_path"), "lora_path"))]
    if payload.get("speaker_gate"):
        inner += [
            "--speaker-gate",
            "--speaker-threshold", str(payload.get("speaker_threshold") or 0.82),
            "--speaker-max-attempts", str(payload.get("speaker_max_attempts") or 2),
            "--speaker-python", str(_trusted_existing_path("CASTLE_VOICE_LAB_PYTHON", Path.home() / ".local/share/web-comfyui/integrations/voice/.venv/bin/python", "speaker_python")),
            "--speaker-script", str(_trusted_existing_path("CASTLE_VOXCPM_SPEAKER_SCRIPT", WEBUI_ROOT / "speaker_similarity.py", "speaker_script")),
        ]
    subprocess.check_call(gpu_queue_cmd(inner, payload), cwd=str(worker.parent))
    if _cancelled(cancel_file):
        raise KeyboardInterrupt()
    _atomic_copy(tmp_output, output)
    _atomic_copy(tmp_sidecar, sidecar)
    return [
        {
            "path": str(output),
            "name": output.name,
            "artifact_type": "audio",
            "output_name": "audio",
            "metadata": {
                "workflow": "castle.voxcpm_tts",
                "mode": str(payload.get("mode") or ""),
                "profile": str(payload.get("profile") or ""),
            },
        }
    ]


def run_castle_voxcpm_tts_batch(payload: dict, work_dir: Path, cancel_file: Path) -> list[dict]:
    voxcpm_python = _trusted_existing_path("CASTLE_VOXCPM_PYTHON", Path.home() / ".local/share/web-comfyui/integrations/voice/voxcpm/.venv/bin/python", "voxcpm_python")
    worker = _trusted_existing_path("CASTLE_VOXCPM_SCRIPT", WEBUI_ROOT / "voxcpm_tts.py", "worker_script")
    model_path = _trusted_existing_path("CASTLE_VOXCPM_MODEL_PATH", Path.home() / ".local/share/web-comfyui/integrations/voice/voxcpm/models/VoxCPM2", "model_path", file=False)
    output_dir = _safe_output_dir(payload.get("output_dir"), "output_dir")
    segments = payload.get("segments") if isinstance(payload.get("segments"), list) else []
    if not segments:
        raise ValueError("segments is required")
    segments_path = work_dir / "segments.json"
    _write_text(segments_path, json.dumps(segments, ensure_ascii=False, indent=2))

    manifest_name = str(payload.get("manifest_name") or "manifest.json").strip()
    if payload.get("redo"):
        if len(segments) != 1:
            raise ValueError("redo requires exactly one segment")
        index = int(segments[0].get("index") or 0)
        if index <= 0:
            raise ValueError("redo segment index is required")
        current = output_dir / f"seg_{index:02d}.wav"
        if current.exists():
            shutil.copy2(current, output_dir / f"seg_{index:02d}_old.wav")
        manifest_name = f"manifest.redo_{index:02d}.json"
    if manifest_name != Path(manifest_name).name or not manifest_name.endswith(".json"):
        raise ValueError("manifest_name must be a json basename")

    seed = payload.get("seed")
    inner = [
        str(voxcpm_python),
        str(worker),
        "--mode", str(payload.get("mode") or "lora"),
        "--segments-json", str(segments_path),
        "--output", str(output_dir),
        "--job-id", str(payload.get("_run_id") or payload.get("_task_id") or "orchestrator"),
        "--workflow", str(payload.get("workflow") or "castle-voxcpm-tts-batch-web"),
        "--manifest-name", manifest_name,
        "--model-path", str(model_path),
        "--language", str(payload.get("language") or ""),
        "--accent", str(payload.get("accent") or ""),
        "--profile", str(payload.get("profile") or ""),
        "--control", str(payload.get("control") or ""),
        "--cfg", str(payload.get("cfg") or 2.0),
        "--steps", str(payload.get("steps") or 10),
        "--cancel-file", str(cancel_file),
        "--candidates", str(max(0, int(payload.get("candidates") or 0))),
    ]
    if seed not in (None, "", -1, "-1", "None", "none"):
        inner += ["--seed", str(seed)]
    if payload.get("reference_audio"):
        inner += ["--reference-audio", str(_safe_existing_input_path(payload.get("reference_audio"), "reference_audio"))]
    if payload.get("prompt_text"):
        inner += ["--prompt-text", str(payload.get("prompt_text") or "")]
    if payload.get("lora_path"):
        inner += ["--lora-path", str(_safe_existing_input_dir_or_file(payload.get("lora_path"), "lora_path"))]
    if payload.get("speaker_gate"):
        inner += [
            "--speaker-gate",
            "--speaker-threshold", str(payload.get("speaker_threshold") or 0.82),
            "--speaker-max-attempts", str(payload.get("speaker_max_attempts") or 2),
            "--speaker-python", str(_trusted_existing_path("CASTLE_VOICE_LAB_PYTHON", Path.home() / ".local/share/web-comfyui/integrations/voice/.venv/bin/python", "speaker_python")),
            "--speaker-script", str(_trusted_existing_path("CASTLE_VOXCPM_SPEAKER_SCRIPT", WEBUI_ROOT / "speaker_similarity.py", "speaker_script")),
        ]
    subprocess.check_call(gpu_queue_cmd(inner, payload), cwd=str(worker.parent))
    if _cancelled(cancel_file):
        raise KeyboardInterrupt()

    manifest_path = output_dir / manifest_name
    manifest = {}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        manifest = {}
    manifest_segments = manifest.get("segments") if isinstance(manifest.get("segments"), list) else []
    artifacts = []
    for item in manifest_segments:
        name = Path(str(item.get("file") or "")).name
        if not name:
            continue
        artifacts.append({
            "path": str(output_dir / name),
            "name": name,
            "artifact_type": "audio",
            "output_name": f"seg_{int(item.get('index') or 0):02d}",
            "metadata": {
                "workflow": "castle.voxcpm_tts_batch",
                "file": name,
                "index": int(item.get("index") or 0),
                "passed": bool(item.get("passed")),
            },
        })
    artifacts.append({
        "path": str(manifest_path),
        "name": manifest_path.name,
        "artifact_type": "json",
        "output_name": "manifest",
        "metadata": {
            "workflow": "castle.voxcpm_tts_batch",
            "manifest": manifest_path.name,
            "count": len(manifest_segments),
            "files": [Path(str(item.get("file") or "")).name for item in manifest_segments if item.get("file")],
        },
    })
    return artifacts


def _resolve_proxy_source(payload: dict) -> dict:
    source_artifact_id = str(payload.get("source_artifact_id") or "").strip()
    declared_hash = str(payload.get("source_content_hash") or "").strip()
    if source_artifact_id:
        with connect() as conn:
            row = conn.execute("select * from artifacts where id = ?", (source_artifact_id,)).fetchone()
            if row is None:
                raise ValueError("source_artifact_id artifact not found")
            path = artifact_abs_path(conn, row)
            stored_hash = str(row["content_hash"] or "")
        if path is None or not path.is_file():
            raise ValueError("source_artifact_id artifact file is missing")
        if not _path_is_allowed(path, _task_allowed_roots()):
            raise ValueError("source_artifact_id artifact file is outside allowed orchestrator roots")
        actual_hash = _file_sha256(path)
        if stored_hash != actual_hash:
            raise ValueError("source_artifact_id artifact file content_hash mismatch")
        if declared_hash and declared_hash != actual_hash:
            raise ValueError("source_content_hash mismatch")
        return {
            "path": path,
            "content_hash": actual_hash,
            "source_artifact_id": source_artifact_id,
            "identity": {"kind": "artifact", "artifact_id": source_artifact_id, "content_hash": actual_hash},
        }
    if not declared_hash:
        raise ValueError("source_content_hash is required")
    source = _safe_existing_input_path(payload.get("source_path"), "source_path")
    actual_hash = _file_sha256(source)
    if declared_hash != actual_hash:
        raise ValueError("source_content_hash mismatch")
    return {
        "path": source,
        "content_hash": actual_hash,
        "source_artifact_id": "",
        "identity": {"kind": "path", "path_hash": hashlib.sha256(str(source).encode("utf-8")).hexdigest(), "content_hash": actual_hash},
    }


def _proxy_input_hash(
    source: dict,
    settings: dict,
    implementation_hash: str,
) -> str:
    if source["source_artifact_id"]:
        return canonical_proxy_input_hash(
            source["source_artifact_id"],
            source["content_hash"],
            implementation_hash,
        )
    return input_hash({
        "workflow": "artifact.make_proxy",
        "role": "proxy",
        "task_schema_version": PROXY_TASK_SCHEMA_VERSION,
        "implementation_hash": implementation_hash,
        "source": source["identity"],
        "settings": settings,
    })


def _proxy_reusable_output(source: dict, proxy_hash: str, artifact_type: str) -> list[dict] | None:
    with connect() as conn:
        rows = conn.execute(
            """
            select * from artifacts
            where json_extract(metadata_json, '$.workflow') = 'artifact.make_proxy'
              and json_extract(metadata_json, '$.role') = 'proxy'
              and json_extract(metadata_json, '$.source_content_hash') = ?
              and json_extract(metadata_json, '$.input_hash') = ?
              and artifact_type = ?
            order by created_at desc
            """,
            (source["content_hash"], proxy_hash, artifact_type),
        ).fetchall()
        for row in rows:
            path = artifact_abs_path(conn, row)
            if path and path.is_file():
                metadata = json.loads(row["metadata_json"] or "{}")
                metadata["idempotent_reuse"] = True
                metadata["reused_from_artifact_id"] = row["id"]
                metadata["reused_from_task_id"] = row["producing_task_id"]
                return [{
                    "path": str(path),
                    "name": path.name,
                    "artifact_type": artifact_type,
                    "output_name": "review_proxy",
                    "metadata": metadata,
                    "tags": ["role:proxy"],
                }]
    return None


def run_artifact_make_proxy(payload: dict, work_dir: Path, cancel_file: Path) -> list[dict]:
    allowed = {
        "source_path", "source_artifact_id", "source_content_hash",
        "output_name", "task_schema_version", "implementation_hash",
        *CANONICAL_REVIEW_PROXY_SETTINGS,
    }
    unknown = sorted(key for key in payload if not str(key).startswith("_") and key not in allowed)
    if unknown:
        raise ValueError(f"artifact.make_proxy unknown fields: {', '.join(unknown)}")
    declared_schema = str(
        payload.get("task_schema_version") or PROXY_TASK_SCHEMA_VERSION)
    if declared_schema != PROXY_TASK_SCHEMA_VERSION:
        raise ValueError("artifact.make_proxy task schema is noncanonical")
    implementation_hash = canonical_proxy_implementation_hash(
        os.environ.get("FFMPEG_BIN"))
    declared_implementation_hash = str(
        payload.get("implementation_hash") or "").strip()
    if (
        declared_implementation_hash
        and declared_implementation_hash != implementation_hash
    ):
        raise ValueError(
            "artifact.make_proxy implementation hash mismatch; "
            "replan the task under the current proxy executor"
        )
    settings = {
        key: str(payload.get(key) or value)
        for key, value in CANONICAL_REVIEW_PROXY_SETTINGS.items()
    }
    if settings != CANONICAL_REVIEW_PROXY_SETTINGS:
        raise ValueError("artifact.make_proxy settings are noncanonical")
    source_info = _resolve_proxy_source(payload)
    source = source_info["path"]
    is_video = source.suffix.lower() in VIDEO_EXTS
    fallback_name = f"{source.stem}_proxy.mp4" if is_video else f"{source.stem}_proxy{source.suffix or '.bin'}"
    output_name = _safe_reel_output_name(payload.get("output_name"), fallback_name)
    if is_video and Path(output_name).suffix.lower() not in VIDEO_EXTS:
        output_name = f"{Path(output_name).stem}.mp4"
    artifact_type = "video" if is_video else ("audio" if source.suffix.lower() in {".wav", ".mp3", ".m4a", ".flac", ".ogg"} else "file")
    proxy_hash = _proxy_input_hash(
        source_info, settings, implementation_hash)
    reusable = (
        _proxy_reusable_output(source_info, proxy_hash, artifact_type)
        if _runner_internal_reuse_allowed(payload) else None
    )
    if reusable:
        _record_task_event(
            payload,
            "task.idempotent_reuse",
            "Reused artifact proxy",
            {"input_hash": proxy_hash, "source_content_hash": source_info["content_hash"]},
        )
        return reusable
    out = (work_dir / "tmp" / output_name).resolve()
    execute_canonical_proxy(
        source=source,
        output=out,
        work_dir=work_dir,
        cancel_file=cancel_file,
        is_video=is_video,
        ffmpeg_bin=os.environ.get("FFMPEG_BIN"),
        run_cpu_child=_run_cpu_child,
    )

    return [
        {
            "path": str(out),
            "name": out.name,
            "artifact_type": artifact_type,
            "output_name": "review_proxy",
            "metadata": {
                "workflow": "artifact.make_proxy",
                "role": "proxy",
                "task_schema_version": PROXY_TASK_SCHEMA_VERSION,
                "implementation_hash": implementation_hash,
                "input_hash": proxy_hash,
                "source_artifact_id": source_info["source_artifact_id"],
                "source_content_hash": source_info["content_hash"],
                "source_name": _sanitize_error_text(source.name, 120),
                "settings": settings,
            },
            "tags": ["role:proxy"],
        }
    ]


def run_task(task_type: str, payload: dict, work_dir: Path, cancel_file: Path) -> list[dict]:
    if task_type == "castle.project_pack_validate":
        return run_project_pack_validate(payload, work_dir, cancel_file)
    if task_type == "castle.project_contract_stage":
        return run_project_contract_stage(payload, work_dir, cancel_file)
    if task_type == "mock.echo":
        return run_mock_echo(payload, work_dir, cancel_file)
    if task_type == "mock.gpu_echo":
        return run_mock_gpu_echo(payload, work_dir, cancel_file)
    if task_type == "ai_sister.reel_assemble_only":
        return run_ai_sister_reel_assemble(payload, work_dir, cancel_file)
    if task_type == "castle.reel_runtime_verify":
        return run_castle_reel_runtime_verify(payload, work_dir, cancel_file)
    if task_type == "castle.reel_stage_cpu":
        return run_castle_reel_native_stage(
            payload, work_dir, cancel_file, expected_lane="cpu")
    if task_type == "castle.reel_stage_gpu":
        return run_castle_reel_native_stage(
            payload, work_dir, cancel_file, expected_lane="gpu")
    if task_type == "castle.reel_prepare_render":
        return run_castle_reel_prepare_render(payload, work_dir, cancel_file)
    if task_type == "castle.voice_speak_guarded":
        return run_castle_voice_speak_guarded(payload, work_dir, cancel_file)
    if task_type == "castle.reel_cover":
        return run_castle_reel_cover(payload, work_dir, cancel_file)
    if task_type == "castle.reel_assemble":
        return run_castle_reel_assemble(payload, work_dir, cancel_file)
    if task_type == "castle.reel_qc":
        return run_castle_reel_qc(payload, work_dir, cancel_file)
    if task_type == "ai_sister.render_sisters":
        return run_ai_sister_render_sisters(payload, work_dir, cancel_file)
    if task_type == "ai_sister.render_host":
        return run_ai_sister_render_host(payload, work_dir, cancel_file)
    if task_type == "castle.voxcpm_tts":
        return run_castle_voxcpm_tts(payload, work_dir, cancel_file)
    if task_type == "castle.voxcpm_tts_batch":
        return run_castle_voxcpm_tts_batch(payload, work_dir, cancel_file)
    if task_type == "artifact.make_proxy":
        return run_artifact_make_proxy(payload, work_dir, cancel_file)
    raise ValueError(f"Unsupported task type: {task_type}")
