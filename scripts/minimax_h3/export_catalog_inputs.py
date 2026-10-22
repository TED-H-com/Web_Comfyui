#!/usr/bin/env python3
"""Emit non-secret H3 descriptor/readiness inputs for catalog review."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "webui"))

from minimax_h3_readiness import (  # noqa: E402
    MiniMaxH3ReadinessConfig,
    audit_minimax_h3_readiness,
    load_manifest,
)
from provider_fabric.h3_catalog import (  # noqa: E402
    H3_VARIANT_CONTRACTS,
    h3_feature_descriptors,
    h3_golden_id,
    h3_readiness_by_variant,
)


def main() -> int:
    config = MiniMaxH3ReadinessConfig()
    workflow_revision = load_manifest(config.workflow_manifest_path).revision
    snapshot = audit_minimax_h3_readiness(config)
    payload = {
        "descriptors": list(h3_feature_descriptors()),
        "readiness": h3_readiness_by_variant(
            snapshot,
            source_revision=f"minimax-h3-{workflow_revision}",
        ),
        # Golden evidence is intentionally never minted by a readiness audit.
        "evidence": {},
        "requiredEvidenceIds": {
            variant_id: h3_golden_id(contract_id)
            for variant_id, contract_id in H3_VARIANT_CONTRACTS.items()
        },
        "publicationReady": False,
        "evidenceRequired": True,
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
