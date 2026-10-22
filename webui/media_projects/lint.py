"""Deterministic production-pack lint and fail-closed pre-spend gate."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable

from provider_fabric.models import CapabilityKind, VisualClass

from .contracts import (
    ConsentState,
    DialogueMode,
    EntityKind,
    EvidenceKind,
    ExecutionDomain,
    ProductionPack,
    RouteVariant,
    StatePhase,
)


_VISUAL_CAPABILITIES = {
    CapabilityKind.TEXT_TO_IMAGE,
    CapabilityKind.IMAGE_TO_IMAGE,
    CapabilityKind.IMAGE_TO_VIDEO,
    CapabilityKind.TEXT_TO_VIDEO,
    CapabilityKind.LIP_SYNC,
    CapabilityKind.DANCE,
    CapabilityKind.MULTI_SUBJECT_VIDEO,
}
_INPUT_REQUIRED_CAPABILITIES = {
    CapabilityKind.IMAGE_TO_IMAGE,
    CapabilityKind.IMAGE_TO_VIDEO,
    CapabilityKind.LIP_SYNC,
    CapabilityKind.DANCE,
    CapabilityKind.MULTI_SUBJECT_VIDEO,
}
_VOICE_CAPABILITIES = {CapabilityKind.TEXT_TO_SPEECH, CapabilityKind.VOICE_CLONE}
_EXECUTABLE_PROVIDER_DOMAINS = {
    "local_comfy": ExecutionDomain.LOCAL,
    "autoip": ExecutionDomain.EXTERNAL,
}


@dataclass(frozen=True, slots=True)
class GateFinding:
    code: str
    path: str
    message: str
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "path": self.path,
            "message": self.message,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class GateReport:
    ok: bool
    fails: tuple[GateFinding, ...]
    warns: tuple[GateFinding, ...]
    evidence: tuple[str, ...]
    pack_hash: str
    minimum_estimated_credits: int | None
    contract_version: str = "castle.production-pack-gate.v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "fails": [item.to_dict() for item in self.fails],
            "warns": [item.to_dict() for item in self.warns],
            "evidence": list(self.evidence),
            "pack_hash": self.pack_hash,
            "minimum_estimated_credits": self.minimum_estimated_credits,
            "contract_version": self.contract_version,
        }


def _finding(code: str, path: str, message: str, *evidence: str) -> GateFinding:
    return GateFinding(code, path, message, tuple(evidence))


def _sort_findings(findings: Iterable[GateFinding]) -> tuple[GateFinding, ...]:
    return tuple(sorted(findings, key=lambda item: (item.path, item.code, item.message, item.evidence)))


def lint_production_pack(pack: ProductionPack) -> GateReport:
    """Validate references, rights, routes, state, timing, and budget.

    A green report means only that the immutable plan is structurally eligible
    to reach the route/quote stage.  It is not a quality verdict and does not
    authorize a paid provider submission or publishing.
    """

    if not isinstance(pack, ProductionPack):
        raise TypeError("pack must be a ProductionPack")

    fails: list[GateFinding] = []
    warns: list[GateFinding] = []
    used_evidence: set[str] = set()

    def unique(items, attr: str, path: str) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for index, item in enumerate(items):
            key = getattr(item, attr)
            if key in result:
                fails.append(_finding("duplicate_id", f"{path}[{index}]", f"Duplicate {attr}: {key}"))
            else:
                result[key] = item
        return result

    entities = unique(pack.entities, "entity_id", "entities")
    evidence_items = unique(pack.evidence, "evidence_id", "evidence")
    routes = unique(pack.route_variants, "route_variant_id", "route_variants")
    scenes = unique(pack.scenes, "scene_id", "scenes")
    shots = unique(pack.shots, "shot_id", "shots")

    for provider_id in pack.project.provider_allowlist:
        if provider_id not in _EXECUTABLE_PROVIDER_DOMAINS:
            fails.append(_finding(
                "unknown_provider",
                "project.provider_allowlist",
                f"Provider has no executable adapter in this contract version: {provider_id}.",
            ))

    # Registry-level evidence and route readiness checks happen even when a bad
    # route is currently unused: entries in this array claim to be eligible.
    for route_index, route in enumerate(pack.route_variants):
        path = f"route_variants[{route_index}]"
        if route.provider_id not in pack.project.provider_allowlist:
            fails.append(_finding("unknown_provider", f"{path}.provider_id", "Provider is outside the project allowlist."))
        expected_domain = _EXECUTABLE_PROVIDER_DOMAINS.get(route.provider_id)
        if expected_domain is None:
            fails.append(_finding("unknown_provider", f"{path}.provider_id", "Provider has no executable adapter in this contract version."))
        elif route.execution_domain is not expected_domain:
            fails.append(_finding("provider_domain_mismatch", f"{path}.execution_domain", "Provider execution domain is server-owned and does not match."))
        if route.execution_domain is ExecutionDomain.EXTERNAL and not pack.project.allow_external_providers:
            fails.append(_finding("external_provider_forbidden", path, "External providers are disabled for this project."))
        if route.execution_domain is ExecutionDomain.EXTERNAL and route.estimated_credits is None:
            finding = _finding(
                "route_quote_required",
                path,
                "This inert external route requires an exact live quote and explicit confirmation before spending.",
            )
            if pack.project.max_credits is None:
                warns.append(finding)
            else:
                fails.append(_finding(
                    "route_cost_unknown",
                    path,
                    "An external route requires a bounded credit estimate under a fixed project cap.",
                ))
        if (
            route.execution_domain is ExecutionDomain.EXTERNAL
            and route.estimated_credits is not None
            and pack.project.max_credits is not None
            and route.estimated_credits > pack.project.max_credits
        ):
            fails.append(_finding("route_exceeds_hard_cap", path, "This route estimate exceeds the entire project credit cap."))
        if route.execution_domain is ExecutionDomain.EXTERNAL and pack.project.max_credits == 0:
            fails.append(_finding("external_budget_zero", path, "A paid external route cannot be eligible under a zero-credit budget."))
        route_evidence = evidence_items.get(route.execution_evidence_id)
        if route.execution_basis == "technical_compatibility":
            if route_evidence is None:
                fails.append(_finding(
                    "technical_evidence_missing",
                    f"{path}.technical_evidence_id",
                    "Route technical compatibility evidence is missing.",
                ))
            elif route_evidence.kind is not EvidenceKind.TECHNICAL_COMPATIBILITY:
                fails.append(_finding(
                    "technical_evidence_wrong_kind",
                    f"{path}.technical_evidence_id",
                    "Technical execution evidence must be kind technical_compatibility.",
                ))
            else:
                used_evidence.add(route_evidence.evidence_id)
        else:
            if not route.release_ready:
                fails.append(_finding("route_not_release_ready", path, "Only release-ready quality-golden variants can enter a production pack."))
            if route_evidence is None:
                fails.append(_finding("golden_evidence_missing", f"{path}.golden_evidence_id", "Route golden evidence is missing."))
            elif route_evidence.kind is not EvidenceKind.GOLDEN:
                fails.append(_finding("golden_evidence_wrong_kind", f"{path}.golden_evidence_id", "Route evidence must be kind golden."))
            else:
                used_evidence.add(route_evidence.evidence_id)

    # Consent records are opaque references; only their private evidence IDs
    # and exact execution scopes belong in the production pack.
    for entity_index, entity in enumerate(pack.entities):
        path = f"entities[{entity_index}]"
        if entity.kind is EntityKind.UNKNOWN:
            fails.append(_finding("entity_kind_unknown", path, "Unknown entity kinds cannot enter generation."))
        if entity.visual_class is VisualClass.UNKNOWN:
            fails.append(_finding("visual_class_unknown", path, "Unclassified entities cannot enter generation."))
        if entity.consent_state is ConsentState.REVOKED:
            fails.append(_finding("consent_revoked", path, "Entity consent or rights have been revoked."))
        elif not entity.consent_state.executable:
            fails.append(_finding("consent_unverified", path, "Entity consent/rights are not executable."))
        if entity.consent_state is ConsentState.NOT_REQUIRED and entity.kind is EntityKind.CONSENTING_PERSON:
            fails.append(_finding("person_consent_required", path, "A real person cannot use not_required consent."))
        if entity.consent_state in {ConsentState.SELF_OWNED, ConsentState.DOCUMENTED}:
            if not entity.consent_ref:
                fails.append(_finding("consent_ref_missing", f"{path}.consent_ref", "Approved consent requires an opaque consent reference."))
            if not entity.consent_evidence_ids:
                fails.append(_finding("consent_evidence_required", f"{path}.consent_evidence_ids", "Approved consent requires private evidence."))
        elif entity.consent_evidence_ids:
            warns.append(_finding("unused_consent_evidence", f"{path}.consent_evidence_ids", "Consent evidence is present for a non-approved state."))
        for evidence_id in entity.consent_evidence_ids:
            evidence = evidence_items.get(evidence_id)
            if evidence is None:
                fails.append(_finding("unknown_consent_evidence", f"{path}.consent_evidence_ids", f"Unknown evidence: {evidence_id}"))
            elif evidence.kind not in {EvidenceKind.CONSENT, EvidenceKind.RIGHTS} or not evidence.is_private:
                fails.append(_finding("invalid_consent_evidence", f"{path}.consent_evidence_ids", "Consent evidence must be private consent/rights evidence."))
            else:
                used_evidence.add(evidence_id)
        if entity.kind is EntityKind.CONSENTING_PERSON:
            if entity.visual_class is not VisualClass.REALISTIC:
                warns.append(_finding("person_class_review", path, "A consenting person normally maps to realistic visual class; review explicitly."))
            if entity.consent_state not in {ConsentState.SELF_OWNED, ConsentState.DOCUMENTED}:
                fails.append(_finding("person_consent_required", path, "A real person requires documented or self-owned consent."))
            if not entity.profile_asset_id:
                fails.append(_finding("person_profile_required", path, "A real person requires an exact profile asset."))

    ordered_scenes = sorted(pack.scenes, key=lambda scene: (scene.ordinal, scene.scene_id))
    scene_ordinals = [scene.ordinal for scene in ordered_scenes]
    if len(scene_ordinals) != len(set(scene_ordinals)):
        fails.append(_finding("scene_order_invalid", "scenes", "Scene ordinals must be unique."))
    referenced_shots: list[str] = []
    previous = None
    for scene in ordered_scenes:
        scene_path = f"scene:{scene.scene_id}"
        referenced_shots.extend(scene.shot_ids)
        if previous is not None and dict(previous.state_after) != dict(scene.state_before):
            changed_keys = sorted(set(previous.state_after) | set(scene.state_before))
            fails.append(_finding(
                "scene_state_chain_broken",
                f"{scene_path}.state_before",
                f"Scene state must exactly equal the preceding state_after; review keys: {', '.join(changed_keys)}.",
                previous.scene_id,
                scene.scene_id,
            ))
        previous = scene
        ordinals: list[int] = []
        for shot_id in scene.shot_ids:
            shot = shots.get(shot_id)
            if shot is None:
                fails.append(_finding("unknown_shot", scene_path, f"Unknown shot: {shot_id}"))
                continue
            if shot.scene_id != scene.scene_id:
                fails.append(_finding("shot_scene_mismatch", f"shot:{shot_id}", "Shot scene_id disagrees with scene membership."))
            ordinals.append(shot.ordinal)
        if ordinals != sorted(ordinals) or len(ordinals) != len(set(ordinals)):
            fails.append(_finding("shot_order_invalid", scene_path, "Shot ordinals must be unique and ascending within a scene."))

    memberships = Counter(referenced_shots)
    duplicate_membership = sorted(shot_id for shot_id, count in memberships.items() if count > 1)
    if duplicate_membership:
        fails.append(_finding("shot_membership_duplicate", "scenes", f"Shots appear in multiple scene slots: {', '.join(duplicate_membership)}."))
    unreferenced = sorted(set(shots) - set(referenced_shots))
    if unreferenced:
        fails.append(_finding("shot_unreferenced", "shots", f"Shots are not owned by a scene: {', '.join(unreferenced)}."))

    used_routes: set[str] = set()
    minimum_credits = 0
    minimum_credit_known = True

    def validate_route(
        route_id: str,
        *,
        path: str,
        purpose: str,
        required_capabilities: set[CapabilityKind],
        required_options: frozenset[str],
        selected_entities: list[Any],
    ) -> RouteVariant | None:
        route = routes.get(route_id)
        if route is None:
            fails.append(_finding("unknown_route_variant", path, f"Unknown route variant: {route_id}"))
            return None
        used_routes.add(route_id)
        if route.capability not in required_capabilities:
            fails.append(_finding("route_capability_mismatch", path, f"The {purpose} route capability does not match the shot requirement."))
        classes = {entity.visual_class for entity in selected_entities}
        if not classes.issubset(route.visual_classes):
            fails.append(_finding("route_visual_class_mismatch", path, f"The {purpose} route is not proven for every selected visual class."))
        if not required_options.issubset(route.option_keys):
            missing = sorted(required_options - route.option_keys)
            fails.append(_finding("route_options_missing", path, f"The {purpose} route lacks options: {', '.join(missing)}."))
        for entity in selected_entities:
            if route.provider_id not in entity.allowed_provider_ids:
                fails.append(_finding(
                    "provider_scope_denied",
                    path,
                    f"Entity {entity.entity_id} is not approved for provider {route.provider_id}.",
                ))
            if route.capability not in entity.allowed_capabilities:
                fails.append(_finding(
                    "capability_scope_denied",
                    path,
                    f"Entity {entity.entity_id} is not approved for capability {route.capability.value}.",
                ))
            if pack.project.usage_scope not in entity.allowed_usage_scopes:
                fails.append(_finding(
                    "usage_scope_denied",
                    path,
                    f"Entity {entity.entity_id} is not approved for project usage scope {pack.project.usage_scope.value}.",
                ))
        return route

    total_duration = 0.0
    for shot_index, shot in enumerate(pack.shots):
        path = f"shots[{shot_index}]"
        total_duration += shot.duration_seconds
        scene = scenes.get(shot.scene_id)
        selected_entities: list[Any] = []
        for entity_id in shot.entity_ids:
            entity = entities.get(entity_id)
            if entity is None:
                fails.append(_finding("unknown_entity", f"{path}.entity_ids", f"Unknown entity: {entity_id}"))
            else:
                selected_entities.append(entity)
        visual_classes = {entity.visual_class for entity in selected_entities}
        if VisualClass.UNKNOWN in visual_classes:
            fails.append(_finding("shot_class_unknown", path, "Shot contains an unclassified entity."))
        if VisualClass.ANIME in visual_classes and VisualClass.REALISTIC in visual_classes:
            fails.append(_finding("mixed_identity_class", path, "Anime and realistic identity pipelines cannot be mixed implicitly."))
        if shot.required_capability not in _VISUAL_CAPABILITIES:
            fails.append(_finding("unsupported_shot_capability", f"{path}.required_capability", "This capability cannot be the visual renderer for a shot."))
        if shot.required_capability in _INPUT_REQUIRED_CAPABILITIES and not shot.input_asset_ids:
            fails.append(_finding("required_input_missing", f"{path}.input_asset_ids", "The selected capability requires an immutable input asset."))
        if shot.required_capability is CapabilityKind.LIP_SYNC and shot.dialogue_mode is DialogueMode.NONE:
            fails.append(_finding("lip_sync_dialogue_missing", f"{path}.dialogue_mode", "Lip sync requires aligned dialogue/audio."))

        visual_costs: list[int] = []
        for route_id in shot.eligible_visual_variant_ids:
            route = validate_route(
                route_id,
                path=f"{path}.eligible_visual_variant_ids",
                purpose="visual",
                required_capabilities={shot.required_capability},
                required_options=shot.required_options,
                selected_entities=selected_entities,
            )
            if route is not None:
                if route.execution_domain is ExecutionDomain.LOCAL:
                    visual_costs.append(route.estimated_credits or 0)
                elif route.estimated_credits is not None:
                    visual_costs.append(route.estimated_credits)
        if visual_costs:
            minimum_credits += min(visual_costs)
        else:
            minimum_credit_known = False

        speaker_ids = {cue.speaker_entity_id for cue in shot.dialogue}
        speaker_entities = [entities[item] for item in speaker_ids if item in entities]
        if shot.dialogue_mode is DialogueMode.SEPARATE_VOICE:
            voice_costs: list[int] = []
            for route_id in shot.eligible_voice_variant_ids:
                route = validate_route(
                    route_id,
                    path=f"{path}.eligible_voice_variant_ids",
                    purpose="voice",
                    required_capabilities=_VOICE_CAPABILITIES,
                    required_options=frozenset(),
                    selected_entities=speaker_entities,
                )
                if route is not None:
                    if route.execution_domain is ExecutionDomain.LOCAL:
                        voice_costs.append(route.estimated_credits or 0)
                    elif route.estimated_credits is not None:
                        voice_costs.append(route.estimated_credits)
            if voice_costs:
                minimum_credits += min(voice_costs)
            else:
                minimum_credit_known = False

        for evidence_id in shot.evidence_ids:
            if evidence_id not in evidence_items:
                fails.append(_finding("unknown_evidence", f"{path}.evidence_ids", f"Unknown evidence: {evidence_id}"))
            else:
                used_evidence.add(evidence_id)
        for claim_index, claim in enumerate(shot.factual_claims):
            claim_path = f"{path}.factual_claims[{claim_index}]"
            for evidence_id in claim.evidence_ids:
                evidence = evidence_items.get(evidence_id)
                if evidence is None:
                    fails.append(_finding("claim_evidence_missing", claim_path, f"Unknown evidence: {evidence_id}"))
                elif evidence.kind not in {EvidenceKind.SOURCE, EvidenceKind.CALIBRATION}:
                    fails.append(_finding("claim_evidence_wrong_kind", claim_path, "Factual claims require source or calibration evidence."))
                elif not evidence.source_hash:
                    fails.append(_finding("claim_evidence_unpinned", claim_path, "Factual claim evidence requires an immutable source hash."))
                else:
                    used_evidence.add(evidence_id)

        prior_end = 0.0
        prior_start = -1.0
        for cue_index, cue in enumerate(shot.dialogue):
            cue_path = f"{path}.dialogue[{cue_index}]"
            if cue.speaker_entity_id not in shot.entity_ids:
                fails.append(_finding("speaker_not_in_shot", cue_path, "Dialogue speaker must be a selected shot entity."))
            if cue.end_seconds > shot.duration_seconds + 1e-6:
                fails.append(_finding("dialogue_out_of_bounds", cue_path, "Dialogue cue extends beyond the shot."))
            if cue.start_seconds < prior_start - 1e-6:
                fails.append(_finding("dialogue_order_invalid", cue_path, "Dialogue cues must be sorted by start time."))
            if cue.start_seconds < prior_end - 1e-6:
                fails.append(_finding("dialogue_overlap", cue_path, "Dialogue cues overlap; split or explicitly mix outside this contract."))
            prior_start = cue.start_seconds
            prior_end = max(prior_end, cue.end_seconds)
            if cue.language.lower() != pack.project.language.lower():
                warns.append(_finding("dialogue_language_review", cue_path, "Dialogue language differs from the project language."))
            speaker = entities.get(cue.speaker_entity_id)
            if speaker is not None:
                if not speaker.voice_use_allowed:
                    fails.append(_finding("voice_scope_denied", cue_path, "Speaker voice use is not approved."))
                if shot.dialogue_mode in {DialogueMode.NATIVE, DialogueMode.SEPARATE_VOICE} and not speaker.voice_asset_id:
                    fails.append(_finding("voice_asset_required", cue_path, "Native or synthesized character speech requires an exact voice asset."))

        if scene is not None:
            before = scene.state_before
            after = scene.state_after
            for key in shot.continuity_keys:
                key_path = f"{path}.continuity_keys"
                if shot.state_phase is StatePhase.BEFORE:
                    if key not in before:
                        fails.append(_finding("continuity_key_missing", key_path, f"Continuity key missing from state_before: {key}."))
                    elif key in shot.continuity_values and shot.continuity_values[key] != before[key]:
                        fails.append(_finding("continuity_value_mismatch", key_path, f"Continuity value disagrees with state_before: {key}."))
                elif shot.state_phase is StatePhase.AFTER:
                    if key not in after:
                        fails.append(_finding("continuity_key_missing", key_path, f"Continuity key missing from state_after: {key}."))
                    elif key in shot.continuity_values and shot.continuity_values[key] != after[key]:
                        fails.append(_finding("continuity_value_mismatch", key_path, f"Continuity value disagrees with state_after: {key}."))
                else:
                    if key not in before or key not in after:
                        fails.append(_finding("continuity_key_missing", key_path, f"During-event continuity requires the key in both snapshots: {key}."))
                    elif before[key] != after[key] and key not in shot.continuity_values:
                        fails.append(_finding("continuity_transition_ambiguous", key_path, f"Changing state needs an explicit shot continuity value: {key}."))
                    elif before[key] == after[key] and key in shot.continuity_values and shot.continuity_values[key] != before[key]:
                        fails.append(_finding("continuity_value_mismatch", key_path, f"Continuity value disagrees with the unchanged scene state: {key}."))

    if (
        minimum_credit_known
        and pack.project.max_credits is not None
        and minimum_credits > pack.project.max_credits
    ):
        fails.append(_finding(
            "credit_budget_exceeded",
            "project.max_credits",
            f"Minimum eligible route estimate {minimum_credits} exceeds the hard budget {pack.project.max_credits}.",
        ))

    duration_delta = abs(total_duration - pack.project.target_duration_seconds)
    tolerance = max(0.5, pack.project.target_duration_seconds * 0.02)
    if duration_delta > tolerance:
        fails.append(_finding(
            "duration_budget_mismatch",
            "project.target_duration_seconds",
            f"Shot total {total_duration:.3f}s differs from target {pack.project.target_duration_seconds:.3f}s.",
        ))

    for route_id in sorted(set(routes) - used_routes):
        warns.append(_finding("unused_route_variant", "route_variants", f"Route variant is not referenced by a shot: {route_id}."))

    sorted_fails = _sort_findings(fails)
    return GateReport(
        ok=not sorted_fails,
        fails=sorted_fails,
        warns=_sort_findings(warns),
        evidence=tuple(sorted(used_evidence)),
        pack_hash=pack.content_hash,
        minimum_estimated_credits=minimum_credits if minimum_credit_known else None,
    )
