"""Exact Oracle Novel Scene Art v2 wire names and public constants."""

from __future__ import annotations

import re
import urllib.parse

from .errors import fail


API_PREFIX = "/api/internal/novel-scene-art"
CAPABILITY_PATH = API_PREFIX + "/capability"
CLAIM_PATH = API_PREFIX + "/claim"
LEASE_PATH = API_PREFIX + "/jobs/{job_id}/lease"
FAIL_PATH = API_PREFIX + "/jobs/{job_id}/fail"
RESULT_INTENT_PATH = API_PREFIX + "/jobs/{job_id}/result-intent"
RESULT_BYTES_PATH = API_PREFIX + "/jobs/{job_id}/result"

SCENE_PACK_SCHEMA = "novel-scene-pack-v2"
RENDER_REQUEST_SCHEMA = "novel-scene-art-render-request-v2"
RENDER_PLAN_SCHEMA = "novel-scene-art-render-plan-v1"
RENDER_RELEASE_SCHEMA = "novel-scene-art-render-release-v1"
CAPABILITY_SCHEMA = "novel-scene-art-worker-capability-v1"
RENDER_RECEIPT_SCHEMA = "novel-scene-art-render-receipt-v1"

RECIPE_ID = "novel-scene-art-anime-v1"
STYLE_PRESET = "adult-anime-3head-v1"
WORKFLOW_CONTRACT_REVISION = "novel-scene-art-workflow-contract-v2"
SEED_POLICY = "server-derived-sha256-52bit-v1"
ADULT_EVIDENCE_POLICY_REVISION = "novel-scene-art-adult-evidence-v1"
OUTPUT_MIME = "image/png"
OUTPUT_WIDTH = 1024
OUTPUT_HEIGHT = 1536
MAX_OUTPUT_BYTES = 24 * 1024 * 1024
MAX_SAFE_INTEGER = 4_503_599_627_370_495

SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$")
SAFE_WORKER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SAFE_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
HASH = re.compile(r"^[a-f0-9]{64}$")


def safe_id(value: object, label: str, *, worker: bool = False) -> str:
    pattern = SAFE_WORKER_ID if worker else SAFE_ID
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        fail("contract_invalid", f"{label} is invalid")
    return value


def digest(value: object, label: str) -> str:
    if not isinstance(value, str) or HASH.fullmatch(value) is None:
        fail("contract_invalid", f"{label} is invalid")
    return value


def error_code(value: object) -> str:
    if not isinstance(value, str) or SAFE_ERROR_CODE.fullmatch(value) is None:
        fail("contract_invalid", "error_code is invalid")
    return value


def _path(template: str, **values: str) -> str:
    encoded = {
        key: urllib.parse.quote(safe_id(value, key), safe="")
        for key, value in values.items()
    }
    return template.format(**encoded)


def lease_path(job_id: str) -> str:
    return _path(LEASE_PATH, job_id=job_id)


def fail_path(job_id: str) -> str:
    return _path(FAIL_PATH, job_id=job_id)


def result_intent_path(job_id: str) -> str:
    return _path(RESULT_INTENT_PATH, job_id=job_id)


def result_bytes_path(job_id: str) -> str:
    return _path(RESULT_BYTES_PATH, job_id=job_id)
