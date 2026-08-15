from types import SimpleNamespace

from gt_engine.utility import choose_candidate, score_candidate


def _candidate(kind, confidence=0.8, target="x.py"):
    return SimpleNamespace(
        evidence_type=kind,
        confidence=confidence,
        target=target,
        provenance=(),
    )


def test_fresh_failure_outranks_low_confidence_localization():
    failure = _candidate("covering_verdict", 0.9)
    location = _candidate("localization", 0.5)
    winner, scores = choose_candidate(
        [location, failure],
        {id(location): "x.py", id(failure): "test failed"},
    )
    assert winner is failure
    assert len(scores) == 2


def test_utility_can_abstain():
    vague = _candidate("unknown", 0.05, target="")
    winner, scores = choose_candidate(
        [vague], {id(vague): "x" * 4000}
    )
    assert winner is None
    assert scores[0].score < 0.08


def test_score_is_deterministic():
    candidate = _candidate("localization", 0.7)
    score = score_candidate(candidate, "x.py")
    assert score == score_candidate(
        candidate, "x.py"
    )
    assert score.freshness == 1.0
    assert score.unresolved_relevance > 0
    assert score.expected_information_gain == 0.7
    assert score.false_positive_risk > 0


def test_sdlc_priority_dominates_confidence_and_registration_wins():
    location = _candidate("localization", 0.5)
    partition = _candidate("def_ref_partition", 0.9)
    winner, _ = choose_candidate(
        [location, partition],
        {id(location): "x.py", id(partition): "definition and references"},
    )
    assert winner is location

    implementation = _candidate("missing_role:implementation", 0.5)
    registration = _candidate("missing_role:registration", 0.5)
    winner, _ = choose_candidate(
        [implementation, registration],
        {id(implementation): "implement", id(registration): "register"},
    )
    assert winner is registration
