"""Five-decision executor (IE-03).

Mechanically testable eligibility for PASS_THROUGH, AUGMENT, REPLACE, REWRITE,
and SUPPRESS. The engine never selects the next Mini-SWE action: this module
takes exactly ONE selected ``ActionRequest`` and returns its interception
decision.

Locked policies (design law):
- Opaque, compound, mixed read/write, analyzer-incomplete, stale, or
  unsupported commands pass through.
- Literal file views and grep retain literal semantics.
- Tests and builds normally retain raw diagnostics and receive structured
  augmentation.
- Typed definition/reference/caller/dependency requests may be replaced only
  under declared completeness.
- Ambiguous or configuration-insensitive graph evidence cannot replace source.
- Unknown failures never become RED blockers.
- SUPPRESS is legal only before side effects, under a certified fresh
  closed-scope blocker, with raw retained in replay storage.
- Only registered FACT owners may contribute model-visible bytes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from .contracts import (
    ActionKind,
    ActionRequest,
    Decision,
    EvidenceArtifact,
    FactOwnerRegistration,
    Fidelity,
    InterceptionDecision,
)


@dataclass(frozen=True)
class AnalyzerState:
    """Deterministic analyzer facts about the selected action at decision time."""

    current_revision: str = ""
    opaque: bool = False
    compound: bool = False
    mixed_read_write: bool = False
    analyzer_incomplete: bool = False
    stale: bool = False
    unsupported: bool = False
    ambiguous: bool = False
    configuration_sensitive: bool = False
    closed_blocker_fresh: bool = False
    certified_replacement: bool = False
    replacement_complete: bool = False
    replacement_ambiguous: bool = False
    replacement_fresh: bool = False
    is_test_or_build: bool = False
    pre_side_effect: bool = False


def decide(
    request: ActionRequest,
    preflight: tuple[EvidenceArtifact, ...],
    owners: Mapping[str, FactOwnerRegistration],
    state: AnalyzerState,
) -> InterceptionDecision:
    """Deterministically decide one selected action.

    Pure and side-effect free. ``preflight`` artifacts may support AUGMENT/
    REPLACE but never decide a REPLACE without certified completeness, a fresh
    revision, and no ambiguity.
    """
    kind = request.kind

    # 1. SUPPRESS — legal only before side effects under a certified fresh
    # closed-scope blocker. Never for general output.
    if kind == ActionKind.SUBMIT and state.pre_side_effect:
        if state.closed_blocker_fresh:
            return _decision(Decision.SUPPRESS, "certified fresh closed-scope blocker", "submit")
        return _decision(
            Decision.PASS_THROUGH,
            "submit preserved; no certified closed-scope blocker",
            "submit",
        )

    # 2. Unsupported, opaque, compound, mixed, stale, or analyzer-incomplete
    # commands pass through with literal semantics.
    if (
        kind == ActionKind.SHELL
        and (state.opaque or state.compound or state.mixed_read_write
             or state.analyzer_incomplete or state.stale or state.unsupported)
    ):
        return _decision(
            Decision.PASS_THROUGH,
            "shell not safely analyzable; literal execution preserved",
            "policy",
        )

    # 3. Literal file views and grep retain literal semantics.
    if kind in (ActionKind.FILE_READ,) and request.requested_fidelity == Fidelity.RAW:
        return _decision(Decision.PASS_THROUGH, "literal file view retains semantics", "literal")

    # 4. Tests and builds retain raw diagnostics + structured augmentation.
    if state.is_test_or_build or kind in (ActionKind.RUN_VERIFICATION, ActionKind.SYNTAX_QUERY):
        return _decision(
            Decision.AUGMENT,
            "raw diagnostics retained; structured augmentation attached",
            "verify",
        )

    # 5. Typed symbol/search requests may REPLACE only under declared
    # completeness + freshness + no ambiguity.
    if kind in (
        ActionKind.SEARCH,
        ActionKind.SYMBOL_DEFINITIONS,
        ActionKind.SYMBOL_REFERENCES,
        ActionKind.SYMBOL_CALLERS,
    ):
        if _certified_replace(preflight, owners, state):
            return _decision(
                Decision.REPLACE,
                "certified complete exact deterministic operation",
                "certified",
            )
        if state.ambiguous or state.analyzer_incomplete:
            return _decision(
                Decision.PASS_THROUGH,
                "ambiguous or incomplete analysis cannot replace source",
                "policy",
            )
        if state.certified_replacement and not state.replacement_complete:
            return _decision(
                Decision.AUGMENT,
                "advisory evidence augments raw acquisition",
                "augment",
            )
        return _decision(Decision.PASS_THROUGH, "no certified replacement; literal acquisition", "policy")

    # 6. Mutation proposal/commit actions run their typed protocol.
    if kind in (ActionKind.CREATE_PROPOSAL, ActionKind.EDIT_PROPOSAL):
        return _decision(
            Decision.AUGMENT,
            "structured mutation proposal returns precommit evidence",
            "mutation",
        )
    if kind == ActionKind.COMMIT_MUTATION:
        return _decision(Decision.AUGMENT, "structured mutation commit with CAS", "mutation")

    # 7. Everything else passes through literally.
    return _decision(Decision.PASS_THROUGH, "unsupported kind passes through", "policy")


def _certified_replace(
    preflight: tuple[EvidenceArtifact, ...],
    owners: Mapping[str, FactOwnerRegistration],
    state: AnalyzerState,
) -> bool:
    """REPLACE requires: complete coverage, fresh revision, no ambiguity, and
    every contributing artifact backed by a registered model-visible owner."""
    if not (state.certified_replacement and state.replacement_complete):
        return False
    if state.replacement_ambiguous or state.ambiguous:
        return False
    if not state.replacement_fresh:
        return False
    for artifact in preflight:
        if not artifact.model_visible:
            continue
        if artifact.owner not in owners:
            return False
        if not owners[artifact.owner].model_visible:
            return False
        if artifact.freshness_revision and artifact.freshness_revision != state.current_revision:
            return False
    return True


def _decision(decision: Decision, reason: str, eligibility: str) -> InterceptionDecision:
    return InterceptionDecision(
        decision=decision,
        reason=reason,
        eligibility=(eligibility,),
        decision_id="",
    )
