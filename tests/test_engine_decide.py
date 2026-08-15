"""IE-03 decide tests: five-decision eligibility truth table."""
from __future__ import annotations

import pytest

from gt_engine.engine.contracts import (
    ActionKind,
    ActionRequest,
    Decision,
    EvidenceArtifact,
    Fidelity,
    FactOwnerRegistration,
)
from gt_engine.engine.decide import AnalyzerState, decide


def make_request(kind=ActionKind.SHELL, **overrides):
    base = dict(
        action_id="a1",
        kind=kind,
        arguments={},
        literal_shell_form="",
        snapshot_token="tok-1",
        configuration_digest="cfg-1",
        requested_fidelity=Fidelity.RAW,
    )
    base.update(overrides)
    return ActionRequest(**base)


def make_owner(name="syntax_result", model_visible=True):
    return FactOwnerRegistration(
        owner=name, role="FACT", producer="py_ast", producer_version="1",
        semantics="syntax", freshness_authority="revision", model_visible=model_visible,
    )


def test_opaque_shell_passes_through():
    d = decide(
        make_request(),
        (),
        {},
        AnalyzerState(opaque=True),
    )
    assert d.decision is Decision.PASS_THROUGH


def test_compound_shell_passes_through():
    d = decide(make_request(), (), {}, AnalyzerState(compound=True))
    assert d.decision is Decision.PASS_THROUGH


def test_literal_file_view_retains_semantics():
    d = decide(
        make_request(ActionKind.FILE_READ, requested_fidelity=Fidelity.RAW),
        (),
        {},
        AnalyzerState(),
    )
    assert d.decision is Decision.PASS_THROUGH


def test_verification_augments_raw_diagnostics():
    d = decide(
        make_request(ActionKind.RUN_VERIFICATION),
        (),
        {},
        AnalyzerState(is_test_or_build=True),
    )
    assert d.decision is Decision.AUGMENT


def test_certified_search_replaces():
    artifact = EvidenceArtifact(
        artifact_id="ev-1", owner="lexical_FTS5", semantics="exact search",
        content={"hits": ["src/x.py:3"]}, freshness_revision="rev-9",
        coverage="complete", model_visible=True,
    )
    d = decide(
        make_request(ActionKind.SEARCH, requested_fidelity=Fidelity.EXACT),
        (artifact,),
        {"lexical_FTS5": make_owner("lexical_FTS5")},
        AnalyzerState(
            current_revision="rev-9",
            certified_replacement=True,
            replacement_complete=True,
            replacement_fresh=True,
        ),
    )
    assert d.decision is Decision.REPLACE


def test_certified_search_ambiguous_never_replaces():
    artifact = EvidenceArtifact(
        artifact_id="ev-1", owner="lexical_FTS5", semantics="exact search",
        content={}, freshness_revision="rev-9", coverage="complete",
        model_visible=True,
    )
    d = decide(
        make_request(ActionKind.SEARCH, requested_fidelity=Fidelity.EXACT),
        (artifact,),
        {"lexical_FTS5": make_owner("lexical_FTS5")},
        AnalyzerState(
            current_revision="rev-9",
            certified_replacement=True,
            replacement_complete=True,
            replacement_fresh=True,
            replacement_ambiguous=True,
        ),
    )
    assert d.decision is Decision.PASS_THROUGH


def test_replace_revoked_on_unregistered_owner():
    artifact = EvidenceArtifact(
        artifact_id="ev-1", owner="unregistered_owner", semantics="x",
        content={}, freshness_revision="rev-9", coverage="complete",
        model_visible=True,
    )
    d = decide(
        make_request(ActionKind.SEARCH, requested_fidelity=Fidelity.EXACT),
        (artifact,),
        {},
        AnalyzerState(
            current_revision="rev-9",
            certified_replacement=True,
            replacement_complete=True,
            replacement_fresh=True,
        ),
    )
    assert d.decision is not Decision.REPLACE


def test_stale_evidence_never_replaces():
    artifact = EvidenceArtifact(
        artifact_id="ev-1", owner="lexical_FTS5", semantics="exact search",
        content={}, freshness_revision="rev-8", coverage="complete",
        model_visible=True,
    )
    d = decide(
        make_request(ActionKind.SEARCH, requested_fidelity=Fidelity.EXACT),
        (artifact,),
        {"lexical_FTS5": make_owner("lexical_FTS5")},
        AnalyzerState(
            current_revision="rev-9",
            certified_replacement=True,
            replacement_complete=True,
            replacement_fresh=False,
        ),
    )
    assert d.decision is not Decision.REPLACE


def test_submit_suppress_only_under_certified_fresh_blocker():
    blocker_case = decide(
        make_request(ActionKind.SUBMIT),
        (),
        {},
        AnalyzerState(pre_side_effect=True, closed_blocker_fresh=True),
    )
    assert blocker_case.decision is Decision.SUPPRESS
    no_blocker = decide(
        make_request(ActionKind.SUBMIT),
        (),
        {},
        AnalyzerState(pre_side_effect=True, closed_blocker_fresh=False),
    )
    assert no_blocker.decision is Decision.PASS_THROUGH


def test_unknown_failure_is_never_a_red_blocker():
    d = decide(
        make_request(ActionKind.SUBMIT),
        (),
        {},
        AnalyzerState(pre_side_effect=True, ambiguous=True),
    )
    assert d.decision is not Decision.SUPPRESS


def test_engine_never_selects_next_action():
    """decide returns exactly one decision for exactly one selected action."""
    req = make_request(ActionKind.SHELL)
    d = decide(req, (), {}, AnalyzerState(opaque=True))
    assert d.decision is Decision.PASS_THROUGH
    assert isinstance(d, object) and d.decision in Decision
