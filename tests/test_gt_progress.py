import gt_engine.progress as progress
from gt_engine.progress import (
    ProgressLedger,
    StallAggregateFact,
    semantic_progress_fingerprint,
    task_information_gain,
)


def test_information_gain_requires_task_linked_evidence_not_output_novelty():
    assert task_information_gain(new_read_anchor=False, diagnostic_gain=False) is False
    assert task_information_gain(new_read_anchor=True, diagnostic_gain=False) is True
    assert task_information_gain(new_read_anchor=False, diagnostic_gain=True) is True


def test_semantic_progress_fingerprint_ignores_novel_commands_but_tracks_task_state():
    first = semantic_progress_fingerprint(
        source_revision="s1",
        changed_paths=(),
        validation_state="unknown",
        diagnostic_fingerprint="",
        project_checks=("pytest -q",),
        validation_debt=True,
    )
    repeated = semantic_progress_fingerprint(
        source_revision="s1",
        changed_paths=(),
        validation_state="unknown",
        diagnostic_fingerprint="",
        project_checks=("pytest -q",),
        validation_debt=True,
    )
    validated = semantic_progress_fingerprint(
        source_revision="s1",
        changed_paths=(),
        validation_state="pass",
        diagnostic_fingerprint="",
        project_checks=("pytest -q",),
        validation_debt=False,
    )

    assert first == repeated
    assert validated != first


def test_stall_aggregate_is_bounded_declarative_and_source_bound():
    fact = StallAggregateFact.create(
        state="STALLED",
        repeated_operation="search",
        concrete_targets=("src/parser.py",),
        repeat_count=3,
        last_returncode=1,
        timeout_observed=False,
        source_revision="s1",
        remaining_calls=17,
        remaining_seconds=420.0,
        unresolved_anchors=("parse_expr",),
        evidence_action=9,
        eligible_call=10,
    )

    rendered = fact.render()
    assert len(rendered) <= 320
    assert "STALLED" in rendered
    assert "src/parser.py" in rendered
    assert "parse_expr" in rendered
    assert "should" not in rendered.lower()


def test_repeated_failure_transitions_before_budget_exhaustion():
    ledger = ProgressLedger(stall_threshold=3)
    assert ledger.observe(
        "same", information_gain=True, changed=False, is_error=True
    ) is None
    assert ledger.observe(
        "same", information_gain=False, changed=False, is_error=True
    ) is None
    transition = ledger.observe(
        "same", information_gain=False, changed=False, is_error=True
    )
    assert transition is not None
    assert transition.current == "CONTRADICTED"


def test_material_change_recovers_stall():
    ledger = ProgressLedger(stall_threshold=2)
    ledger.observe("same", information_gain=True, changed=False, is_error=False)
    stalled = ledger.observe(
        "same", information_gain=False, changed=False, is_error=False
    )
    assert stalled is not None and stalled.current == "STALLED"
    recovered = ledger.observe(
        "new", information_gain=True, changed=True, is_error=False
    )
    assert recovered is not None and recovered.current == "RECOVERED"


def test_budget_risk_only_for_unresolved_stall():
    ledger = ProgressLedger(stall_threshold=2)
    assert ledger.budget_risk(iteration=80, limit=100) is None
    ledger.observe("same", information_gain=True, changed=False, is_error=False)
    ledger.observe("same", information_gain=False, changed=False, is_error=False)
    transition = ledger.budget_risk(iteration=80, limit=100)
    assert transition is not None
    assert transition.current == "BUDGET_RISK"


def test_unresolved_contract_triggers_budget_risk_without_exact_loop():
    ledger = ProgressLedger(stall_threshold=3)
    transition = ledger.budget_risk(
        iteration=80, limit=100, unresolved=True
    )
    assert transition is not None


def test_budget_risk_ratio_can_warn_at_sixty_percent_without_changing_default():
    default = ProgressLedger(stall_threshold=3)
    configured = ProgressLedger(stall_threshold=3)

    assert default.budget_risk(iteration=60, limit=100, unresolved=True) is None
    transition = configured.budget_risk(
        iteration=60,
        limit=100,
        iteration_risk_ratio=0.6,
        unresolved=True,
    )

    assert transition is not None
    assert transition.current == "BUDGET_RISK"
    assert transition.current == "BUDGET_RISK"
    assert transition.reason == "unresolved_contract_near_iteration_limit"


def test_unresolved_contract_triggers_budget_risk_near_time_limit():
    ledger = ProgressLedger(stall_threshold=3)

    transition = ledger.budget_risk(
        iteration=20,
        limit=100,
        unresolved=True,
        remaining_seconds=80.0,
        time_risk_threshold_seconds=90.0,
    )

    assert transition is not None
    assert transition.current == "BUDGET_RISK"
    assert transition.reason == "unresolved_contract_near_time_limit"


def test_novel_activity_cannot_clear_budget_risk_without_task_state_progress():
    ledger = ProgressLedger(stall_threshold=3)
    transition = ledger.budget_risk(iteration=80, limit=100, unresolved=True)
    assert transition is not None and ledger.state == "BUDGET_RISK"

    observed = ledger.observe(
        "new-scratch-output",
        information_gain=True,
        changed=False,
        is_error=False,
    )

    assert observed is None
    assert ledger.state == "BUDGET_RISK"


def test_patch_attempt_is_not_semantic_progress():
    ledger = ProgressLedger(stall_threshold=2)
    ledger.budget_risk(iteration=80, limit=100, unresolved=True)
    observed = ledger.observe(
        "same-semantic-state",
        information_gain=False,
        changed=True,
        semantic_gain=False,
        is_error=False,
    )
    assert observed is None
    assert ledger.state == "BUDGET_RISK"


def test_validation_gain_clears_budget_risk():
    ledger = ProgressLedger(stall_threshold=2)
    ledger.budget_risk(iteration=80, limit=100, unresolved=True)
    observed = ledger.observe(
        "validated-state",
        information_gain=True,
        changed=True,
        semantic_gain=True,
        is_error=False,
    )
    assert observed is not None
    assert observed.current == "RECOVERED"


def test_nonconsecutive_repetition_stalls_within_unchanged_epoch():
    ledger = ProgressLedger(stall_threshold=3)
    ledger.observe("same", information_gain=True, changed=False, is_error=False)
    ledger.observe("other", information_gain=True, changed=False, is_error=False)
    ledger.observe("same", information_gain=False, changed=False, is_error=False)
    ledger.observe("third", information_gain=True, changed=False, is_error=False)
    transition = ledger.observe(
        "same", information_gain=False, changed=False, is_error=False
    )
    assert transition is not None
    assert transition.current == "STALLED"


def test_environment_error_stalls_without_source_contradiction():
    ledger = ProgressLedger(stall_threshold=2)
    ledger.observe("missing-dep", information_gain=True, changed=False, is_error=True)
    transition = ledger.observe(
        "missing-dep",
        information_gain=False,
        changed=False,
        is_error=True,
        contradictory=False,
    )
    assert transition is not None
    assert transition.current == "STALLED"


def test_central_policy_detects_alternating_cycle_only_after_six_observations():
    ledger = ProgressLedger(stall_threshold=3, cycle_threshold=6)
    transitions = []
    for index, signature in enumerate(("a", "b", "a", "b", "a", "b")):
        transitions.append(
            ledger.observe(
                signature,
                information_gain=index < 2,
                changed=False,
                is_error=False,
            )
        )

    assert all(item is None for item in transitions[:5])
    assert transitions[5] is not None
    assert transitions[5].current == "STALLED"
    assert transitions[5].reason == "cyclic_actions_without_information"


def test_repeated_same_state_stall_is_private_until_state_changes():
    ledger = ProgressLedger(stall_threshold=3)
    assert ledger.observe(
        "same", information_gain=True, changed=False, semantic_gain=False, is_error=False
    ) is None
    assert ledger.observe(
        "same", information_gain=False, changed=False, semantic_gain=False, is_error=False
    ) is None
    first = ledger.observe(
        "same", information_gain=False, changed=False, semantic_gain=False, is_error=False
    )
    duplicate = ledger.observe(
        "same", information_gain=False, changed=False, semantic_gain=False, is_error=False
    )

    assert first is not None and first.current == "STALLED"
    assert duplicate is None


def test_action_result_semantics_distinguish_valid_nonzero_observations():
    assert progress.classify_action_result(
        operation="search", executable="rg", return_code=1
    ) is progress.ActionResultKind.SEARCH_NO_MATCH
    assert progress.classify_action_result(
        operation="read", executable="diff", return_code=1
    ) is progress.ActionResultKind.DIFFERENCE
    assert progress.classify_action_result(
        operation="validate", executable="python3", return_code=1
    ) is progress.ActionResultKind.VALIDATION_FAIL
    assert progress.classify_action_result(
        operation="read", executable="xxd", return_code=127
    ) is progress.ActionResultKind.EXECUTION_ERROR


def test_action_result_recognizes_miniswe_timeout_protocol():
    assert progress.classify_action_result(
        operation="validate",
        executable="python3",
        return_code=-1,
        output="RuntimeError: Command timed out after 30.0 seconds",
    ) is progress.ActionResultKind.TIMEOUT


def test_progress_observation_separates_attempt_identity_from_result_identity():
    failed = progress.ProgressObservation.create(
        command="xxd legacy.cob",
        operation="read",
        executable="xxd",
        targets=("legacy.cob",),
        source_revision="s1",
        result_kind=progress.ActionResultKind.EXECUTION_ERROR,
        output="xxd: command not found",
    )
    fallback = progress.ProgressObservation.create(
        command="od legacy.cob",
        operation="read",
        executable="od",
        targets=("legacy.cob",),
        source_revision="s1",
        result_kind=progress.ActionResultKind.SUCCESS,
        output="0000000 123 456",
    )
    repeated = progress.ProgressObservation.create(
        command="od legacy.cob",
        operation="read",
        executable="od",
        targets=("legacy.cob",),
        source_revision="s1",
        result_kind=progress.ActionResultKind.SUCCESS,
        output="0000000 123 456",
    )

    assert failed.attempt_id != fallback.attempt_id
    assert failed.observation_id != fallback.observation_id
    assert fallback.observation_id == repeated.observation_id


def test_progress_attempt_identity_distinguishes_different_commands_with_same_shape():
    """Different experiments must not collapse into one repeated-action loop."""

    first = progress.ProgressObservation.create(
        command="rg -n password .",
        operation="search",
        executable="rg",
        targets=(".",),
        source_revision="s1",
        result_kind=progress.ActionResultKind.SEARCH_NO_MATCH,
        output="",
    )
    second = progress.ProgressObservation.create(
        command="rg -n api_key .",
        operation="search",
        executable="rg",
        targets=(".",),
        source_revision="s1",
        result_kind=progress.ActionResultKind.SEARCH_NO_MATCH,
        output="",
    )
    repeated = progress.ProgressObservation.create(
        command="rg -n password .",
        operation="search",
        executable="rg",
        targets=(".",),
        source_revision="s1",
        result_kind=progress.ActionResultKind.SEARCH_NO_MATCH,
        output="",
    )

    assert first.command_sha256 != second.command_sha256
    assert first.attempt_id != second.attempt_id
    assert first.command_sha256 == repeated.command_sha256
    assert first.attempt_id == repeated.attempt_id
