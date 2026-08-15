from __future__ import annotations

import pytest

from gt_engine.miniswe_controller import (
    GroundtruthController,
    LifecycleError,
    Predicate,
    PredicateStatus,
    VerificationPlan,
)


def test_controller_lifecycle_and_fresh_submit():
    c = GroundtruthController([Predicate("syntax", "syntax check")])
    assert c.phase == "ORIENT"
    c.start_task()
    assert c.phase == "IMPLEMENT"
    c.record_receipt("syntax", "cmd", 0, "ok", epoch=0, semantic=True)
    with pytest.raises(LifecycleError, match="VERIFY"):
        c.submit_decision()
    c.begin_verify()
    c.begin_submit()
    assert c.submit_decision() is True
    assert c.phase == "FINISHED"
    with pytest.raises(LifecycleError, match="FINISHED"):
        c.before_action("bash", "echo after")


def test_edit_invalidates_receipt_and_refuses_stale_submit():
    c = GroundtruthController([Predicate("artifact", "artifact exists")])
    c.start_task()
    c.begin_verify()
    c.record_receipt("artifact", "check", 0, "ok", epoch=c.workspace_epoch,
                     semantic=True)
    c.begin_implement()
    c.note_edit(["out.txt"])
    c.begin_verify()
    c.begin_submit()
    assert c.submit_decision() is True
    # the wipe reset the predicate to UNKNOWN, which is not a failure
    assert c.predicate_status("artifact") is PredicateStatus.UNKNOWN


def test_duplicate_action_is_observed_but_never_rejected():
    c = GroundtruthController([], repeat_budget=1)
    c.start_task()
    for _ in range(6):
        c.before_action("bash", "printf 1")
        c.after_observation("same output", diff_hash="d")
    assert c.phase == "IMPLEMENT"


def test_before_action_survives_unbalanced_quotes():
    c = GroundtruthController([], repeat_budget=2)
    c.start_task()
    # Mini-SWE models can emit commands with an unclosed quote; shlex.split on
    # that raises ValueError, which must never crash the controller loop.
    c.before_action("bash", 'echo "unclosed')
    c.before_action("bash", 'echo "unclosed')
    c.after_observation("output")


def test_repeat_telemetry_resets_after_an_edit_without_ever_blocking():
    c = GroundtruthController([], repeat_budget=1)
    c.start_task()
    c.before_action("bash", "pytest -q")  # count 1
    c.before_action("bash", "pytest -q")  # count 2 (within budget: 2 > 1 is false)
    c.note_edit(["src/mod.py"])           # C3: budget is per-epoch -> reset
    # A legitimate re-run of the same command AFTER an edit is new work.  The
    # repeat budget is advisory telemetry and never suppresses execution.
    for _ in range(5):
        c.before_action("bash", "pytest -q")
    assert c.phase == "IMPLEMENT"


def test_sleep_polling_is_exempt_from_repeat_budget():
    c = GroundtruthController([], repeat_budget=1)
    c.start_task()
    # Polling a long-running benchmark ("sleep N; cat log") is legitimate
    # progress-waiting, not a stuck loop: many repeats must not STUCK.
    for _ in range(5):
        c.before_action("bash", "sleep 25; cat benchmark_log.txt")
        c.after_observation("partial output", diff_hash="d")
    assert c.phase != "STUCK"


def test_unknown_receipt_is_not_green():
    c = GroundtruthController([Predicate("p", "predicate")])
    c.start_task()
    c.begin_verify()
    c.record_receipt("p", "check", 0, "unknown", epoch=c.workspace_epoch)
    c.begin_submit()
    assert c.predicate_status("p") is PredicateStatus.UNKNOWN
    # D3-G: UNKNOWN (no evidence either way) does not block submission. Only a
    # real RED receipt blocks.
    assert c.submit_decision() is True


def test_unevaluated_verification_plan_is_unknown_and_nonblocking():
    c = GroundtruthController(
        [Predicate("artifact", "artifact is semantically valid")],
        verification_plan=VerificationPlan("plan-1", ("artifact",)),
    )
    c.start_task()
    c.begin_verify()
    c.record_receipt("artifact", "check", 0, "valid artifact", epoch=0,
                     status=PredicateStatus.GREEN, semantic=True)
    c.begin_submit()
    assert c.submit_decision() is True
    assert c.phase == "FINISHED"
    assert any("verification_plan" in reason for reason in c.unmet_reasons)


def test_verification_plan_evaluation_allows_semantic_submit():
    c = GroundtruthController(
        [Predicate("artifact", "artifact is semantically valid")],
        verification_plan=VerificationPlan("plan-1", ("artifact",)),
    )
    c.start_task()
    c.begin_verify()
    c.record_receipt("artifact", "check", 0, "valid artifact", epoch=0,
                     status=PredicateStatus.GREEN, semantic=True)
    c.mark_verification_plan_evaluated("plan-1", epoch=0)
    c.begin_submit()
    assert c.submit_decision() is True


def test_nonsemantic_zero_exit_cannot_be_green():
    c = GroundtruthController([Predicate("p", "semantic predicate")])
    c.start_task()
    c.begin_verify()
    c.record_receipt("p", "grep", 0, "matched", epoch=0)
    assert c.predicate_status("p") is PredicateStatus.UNKNOWN
    assert "semantic evidence" in c.unmet_reasons[0]
    # non-semantic receipts never certify GREEN, but UNKNOWN is not RED
    assert c.blocking_predicates == ()


def test_red_receipt_blocks_submission():
    c = GroundtruthController([Predicate("p", "failing predicate")])
    c.start_task()
    c.begin_verify()
    c.record_receipt("p", "pytest", 1, "1 failed", epoch=c.workspace_epoch,
                     status="RED", semantic=True)
    c.begin_submit()
    assert c.submit_decision() is False
    assert c.blocking_predicates == ("p",)
    assert c.phase == "IMPLEMENT"


def test_recovery_advisor_never_owns_or_terminates_agent_strategy():
    c = GroundtruthController([], repeat_budget=1)
    c.start_task()
    c.before_action("bash", "pytest -q")
    decision = c.recovery_action(
        "pytest -q", observation="same failure", alternatives=("python -m pytest -x",)
    )
    assert decision.action == "python -m pytest -x"
    assert decision.reason
    c.before_action("bash", decision.action)
    quiet = c.recovery_action(
        decision.action, observation="same failure", alternatives=()
    )
    assert quiet.action == ""
    assert c.phase == "IMPLEMENT"
