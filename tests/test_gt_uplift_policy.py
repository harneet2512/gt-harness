from __future__ import annotations

from gt_engine.uplift_policy import (
    EvidenceAuthority,
    GTPolicyMode,
    OpportunityDisposition,
    OpportunityKind,
    certify_opportunity,
)


def test_certified_opportunity_requires_nonheuristic_current_concrete_evidence():
    decision = certify_opportunity(
        kind=OpportunityKind.LOCALIZATION_CONTRACTION,
        authority=EvidenceAuthority.HEURISTIC,
        source_revision="s1",
        current_source_revision="s1",
        workspace_revision="w1",
        evidence_ids=("fact-1",),
        concrete_anchors=("src/app.py:run",),
        absent_from_provider_history=True,
        decision_relevant=True,
        eligible_call=2,
        current_call=2,
    )

    assert decision.disposition is OpportunityDisposition.ABSTAIN
    assert "heuristic_evidence" in decision.reason_codes


def test_structural_opportunity_is_certified_only_for_first_eligible_call():
    decision = certify_opportunity(
        kind=OpportunityKind.LOCALIZATION_CONTRACTION,
        authority=EvidenceAuthority.CERTIFIED_STRUCTURAL,
        source_revision="s1",
        current_source_revision="s1",
        workspace_revision="w1",
        evidence_ids=("fact-1",),
        concrete_anchors=("src/app.py:run",),
        absent_from_provider_history=True,
        decision_relevant=True,
        eligible_call=2,
        current_call=2,
    )

    assert decision.certified is True
    assert decision.disposition is OpportunityDisposition.DELIVER_NEXT

    expired = certify_opportunity(
        kind=OpportunityKind.LOCALIZATION_CONTRACTION,
        authority=EvidenceAuthority.CERTIFIED_STRUCTURAL,
        source_revision="s1",
        current_source_revision="s1",
        workspace_revision="w1",
        evidence_ids=("fact-1",),
        concrete_anchors=("src/app.py:run",),
        absent_from_provider_history=True,
        decision_relevant=True,
        eligible_call=2,
        current_call=3,
    )

    assert expired.disposition is OpportunityDisposition.ABSTAIN
    assert "delivery_window_expired" in expired.reason_codes


def test_policy_modes_are_explicit_and_fail_closed():
    assert GTPolicyMode.parse("certified_active") is GTPolicyMode.CERTIFIED_ACTIVE
    assert GTPolicyMode.parse("unknown") is GTPolicyMode.OFF
