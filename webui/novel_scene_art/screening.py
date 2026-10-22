"""Qualified, per-output animated-adult screening boundary."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Mapping, Protocol, Sequence

from .errors import fail
from .manifests import RenderRelease, canonical_json
from .protocol import HASH, SAFE_ID


SCREENING_RECEIPT_SCHEMA = "castle.novel-scene-art-output-screening.v2"


@dataclass(frozen=True, slots=True)
class ScreeningDecision:
    passed: bool
    content_sha256: str
    screening_manifest_sha256: str
    policy_revision: str
    content_class: str
    animated: bool
    real_person_likeness_detected: bool
    minor_or_ambiguous_age_detected: bool
    adult_appearance_confirmed: bool
    identity_match_confirmed: bool
    identity_slots_sha256: str
    receipt_sha256: str

    @classmethod
    def issue(
        cls,
        *,
        passed: bool,
        content_sha256: str,
        screening_manifest_sha256: str,
        policy_revision: str,
        content_class: str,
        animated: bool,
        real_person_likeness_detected: bool,
        minor_or_ambiguous_age_detected: bool,
        adult_appearance_confirmed: bool,
        identity_match_confirmed: bool,
        identity_slots_sha256: str,
    ) -> "ScreeningDecision":
        body = {
            "schema_version": SCREENING_RECEIPT_SCHEMA,
            "passed": passed,
            "content_sha256": content_sha256,
            "screening_manifest_sha256": screening_manifest_sha256,
            "policy_revision": policy_revision,
            "content_class": content_class,
            "animated": animated,
            "real_person_likeness_detected": real_person_likeness_detected,
            "minor_or_ambiguous_age_detected": minor_or_ambiguous_age_detected,
            "adult_appearance_confirmed": adult_appearance_confirmed,
            "identity_match_confirmed": identity_match_confirmed,
            "identity_slots_sha256": identity_slots_sha256,
        }
        return cls(
            passed,
            content_sha256,
            screening_manifest_sha256,
            policy_revision,
            content_class,
            animated,
            real_person_likeness_detected,
            minor_or_ambiguous_age_detected,
            adult_appearance_confirmed,
            identity_match_confirmed,
            identity_slots_sha256,
            hashlib.sha256(canonical_json(body)).hexdigest(),
        )


class OutputScreener(Protocol):
    @property
    def ready(self) -> bool: ...

    def screen(
        self,
        data: bytes,
        release: RenderRelease,
        identity_slots: Sequence[Mapping[str, object]],
    ) -> ScreeningDecision: ...


class UnavailableScreener:
    @property
    def ready(self) -> bool:
        return False

    def screen(
        self,
        data: bytes,
        release: RenderRelease,
        identity_slots: Sequence[Mapping[str, object]],
    ) -> ScreeningDecision:
        del data, release, identity_slots
        fail(
            "output_screening_unavailable",
            "No qualified per-output screening adapter is installed",
            definite_pre_submit=False,
        )


def require_passed_decision(
    decision: ScreeningDecision,
    *,
    data: bytes,
    release: RenderRelease,
    identity_slots: Sequence[Mapping[str, object]],
) -> None:
    boolean_signals = (
        decision.passed,
        decision.animated,
        decision.real_person_likeness_detected,
        decision.minor_or_ambiguous_age_detected,
        decision.adult_appearance_confirmed,
        decision.identity_match_confirmed,
    )
    if (
        any(type(signal) is not bool for signal in boolean_signals)
        or any(
            not isinstance(value, str) or HASH.fullmatch(value) is None
            for value in (
                decision.content_sha256,
                decision.screening_manifest_sha256,
                decision.identity_slots_sha256,
                decision.receipt_sha256,
            )
        )
        or not isinstance(decision.policy_revision, str)
        or SAFE_ID.fullmatch(decision.policy_revision) is None
        or not isinstance(decision.content_class, str)
    ):
        fail(
            "output_screening_rejected",
            "Output screening decision has invalid signal types",
            definite_pre_submit=False,
        )
    identity_slots_sha256 = hashlib.sha256(canonical_json(list(identity_slots))).hexdigest()
    expected = ScreeningDecision.issue(
        passed=decision.passed,
        content_sha256=decision.content_sha256,
        screening_manifest_sha256=decision.screening_manifest_sha256,
        policy_revision=decision.policy_revision,
        content_class=decision.content_class,
        animated=decision.animated,
        real_person_likeness_detected=decision.real_person_likeness_detected,
        minor_or_ambiguous_age_detected=decision.minor_or_ambiguous_age_detected,
        adult_appearance_confirmed=decision.adult_appearance_confirmed,
        identity_match_confirmed=decision.identity_match_confirmed,
        identity_slots_sha256=decision.identity_slots_sha256,
    )
    if (
        decision.receipt_sha256 != expected.receipt_sha256
        or decision.content_sha256 != hashlib.sha256(data).hexdigest()
        or decision.screening_manifest_sha256 != release.screening.sha256
        or decision.policy_revision != release.screening_policy_revision
        or decision.content_class != "fictional_animated_adult"
        or decision.identity_slots_sha256 != identity_slots_sha256
        or not decision.passed
        or not decision.animated
        or decision.real_person_likeness_detected
        or decision.minor_or_ambiguous_age_detected
        or not decision.adult_appearance_confirmed
        or not decision.identity_match_confirmed
    ):
        fail(
            "output_screening_rejected",
            "Output did not pass exact fictional animated adult screening",
            definite_pre_submit=False,
        )
