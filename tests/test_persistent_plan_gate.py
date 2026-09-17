"""The completion gate: refuses while blocking evidence exists, unconditionally."""
from __future__ import annotations

import pytest

from gt_engine.persistent_plan import PersistentPlan, PlanInputs, PlanRow
from gt_engine.persistent_plan.anchors import AnchorResult
from gt_engine.persistent_plan.baseline import BaselineResult
from gt_engine.persistent_plan.gate import (
    MIN_REMAINING_SECONDS,
    MIN_REMAINING_STEPS,
    budget_allows_refusal,
    decide,
    render_directive,
)
from gt_engine.persistent_plan.ledger import build_requirement_ledger

AMPLE = {"remaining_seconds": 3000.0, "remaining_steps": 200}


def test_unlimited_steps_leave_wall_time_reserve_in_force():
    assert budget_allows_refusal(3000.0, None) == (True, "")
    assert budget_allows_refusal(599.0, None) == (False, "time")
    decision = decide(plan=_plan(), unmet_rows=("req-a",), regressions=(),
                      refusals=0, remaining_seconds=3000.0, remaining_steps=None)
    assert not decision.accepted
    assert decision.as_row()["remaining_steps"] is None


def _plan(rows=("req-a", "req-b")) -> PersistentPlan:
    inputs = PlanInputs(
        ledger=build_requirement_ledger(""),
        anchors=AnchorResult(),
        baseline=BaselineResult(status="captured", passed=10, command=("pytest",)),
    )
    return PersistentPlan(
        status="READY",
        inputs=inputs,
        rows=tuple(
            PlanRow(
                row_id=row_id,
                text=f"requirement {row_id}",
                verification_command=f"pytest -k {row_id}",
            )
            for row_id in rows
        ),
    )


def test_a_complete_plan_is_accepted():
    decision = decide(
        plan=_plan(), unmet_rows=(), regressions=(), refusals=0, **AMPLE
    )
    assert decision.accepted
    assert decision.reason == "no_blocking_evidence"
    assert decision.as_row()["completion_proven"] is False


def test_no_blocking_evidence_does_not_claim_unknown_baseline_is_intact():
    decision = decide(plan=_plan(), unmet_rows=(), regressions=(), refusals=0,
                      baseline_status="unknown", **AMPLE)
    assert decision.accepted
    assert decision.reason != "complete"
    assert decision.as_row()["completion_proven"] is False


def test_gate_receipt_keeps_mapping_and_check_evidence_distinct():
    decision = decide(plan=_plan(), unmet_rows=("req-b",), regressions=(), refusals=0,
                      baseline_status="unknown", row_states={"req-a": "CHECK_PASSED", "req-b": "UNVERIFIED"},
                      predicate_mapped_rows=("req-a",), **AMPLE)
    evidence = decision.as_row()["evidence"]
    assert evidence["check_passed_rows"] == ["req-a"]
    assert evidence["unmapped_rows"] == ["req-b"]
    assert evidence["unverified_rows"] == ["req-b"]
    # The row ledger was assessed and says req-b is unverified; claiming
    # "not_established" here is how gate-one read 28 unverified rows beside a
    # submitted_verified terminal.
    assert evidence["completion_assessment"] == "rows_unverified"
    assert evidence["baseline_assessment"] == "unknown"
    assert not decision.accepted


def test_gate_legacy_call_does_not_invent_mapping_evidence():
    decision = decide(plan=_plan(), unmet_rows=(), regressions=(), refusals=0, **AMPLE)
    assert decision.as_row()["evidence"]["mapping_assessment"] == "unavailable"
    assert decision.as_row()["evidence"]["completion_assessment"] == "not_established"
    # With no row ledger supplied, completion is genuinely unassessed and can
    # never be claimed proven.
    assert decision.as_row()["completion_proven"] is False


def test_every_row_verified_proves_completion():
    """completion_proven was hardcoded False, so a fully-verified plan and an
    unverified one journaled identically. Proof also requires a baseline that
    could see the grading signal - the recheck must have produced a
    conservation verdict, which is "intact" (or "regressed", which then
    blocks through regressions). The capture status "captured" never reaches
    the gate: it is what run_baseline records, not what the recheck reports."""
    decision = decide(
        plan=_plan(), unmet_rows=(), regressions=(), refusals=0,
        baseline_status="intact",
        row_states={"req-a": "CHECK_PASSED", "req-b": "PROVEN"}, **AMPLE)
    assert decision.accepted
    row = decision.as_row()
    assert row["completion_proven"] is True
    assert row["evidence"]["completion_assessment"] == "all_rows_verified"
    assert row["evidence"]["proven_rows"] == ["req-b"]


def test_verified_rows_on_a_blind_baseline_are_not_proven():
    """Run 34801009507, bandit-interprocedural-taint-checks: every plan row
    reached CHECK_PASSED and completion_proven journaled true while the
    verifier failed the submission - the baseline was no_tests_observed, so
    the bound checks were ambient checks, never the graded suite. Verified
    rows on a blind baseline are verification against a proxy, not proof."""
    # "unknown"/"incomplete"/"new_failures_unattributed" are recheck reports
    # that observed the suite but could not establish conservation; they are
    # as blind as a baseline that never ran. Only "intact" is sighted.
    for blind in ("no_tests_observed", "probe_failed", "budget_not_checked", "",
                  "unknown", "incomplete", "new_failures_unattributed",
                  "no_test_verdicts", "no_baseline", "timeout"):
        decision = decide(
            plan=_plan(), unmet_rows=(), regressions=(), refusals=0,
            baseline_status=blind,
            row_states={"req-a": "CHECK_PASSED", "req-b": "CHECK_PASSED"}, **AMPLE)
        assert decision.accepted
        row = decision.as_row()
        assert row["completion_proven"] is False, blind
        # The ledger still reports what it saw - honesty changes the claim,
        # not the observation.
        assert row["evidence"]["completion_assessment"] == "all_rows_verified"


def test_unverified_rows_do_not_claim_completion():
    decision = decide(
        plan=_plan(), unmet_rows=(), regressions=(), refusals=0,
        row_states={"req-a": "CHECK_PASSED", "req-b": "UNVERIFIED"}, **AMPLE)
    assert decision.accepted
    assert decision.as_row()["completion_proven"] is False
    assert decision.as_row()["evidence"]["completion_assessment"] == "rows_unverified"


def test_a_regression_disproves_completion_even_with_verified_rows():
    decision = decide(
        plan=_plan(), unmet_rows=(), regressions=("test_widget",), refusals=0,
        row_states={"req-a": "CHECK_PASSED", "req-b": "CHECK_PASSED"}, **AMPLE)
    assert not decision.accepted
    assert decision.as_row()["completion_proven"] is False


def test_unmet_rows_refuse_once_when_there_is_room():
    decision = decide(
        plan=_plan(), unmet_rows=("req-a",), regressions=(), refusals=0, **AMPLE
    )
    assert not decision.accepted
    assert decision.reason == "unmet_plan_rows"
    assert "req-a" in decision.directive
    assert "pytest -k req-a" in decision.directive


def test_a_second_submit_is_refused_while_budget_and_progress_remain():
    """Refusing once made the gate a formality.

    Measured on run 34374028796, task claude-code: refused with rows unmet, then
    accepted the next attempt 34 seconds later with 4,260 seconds and 142 steps
    still available and the same rows still unproven. Seventy-one minutes went
    unused because a counter said the gate had had its turn.
    """
    decision = decide(
        plan=_plan(), unmet_rows=("req-a",), regressions=(), refusals=1,
        refusals_without_progress=0, **AMPLE
    )
    assert not decision.accepted
    assert decision.reason == "unmet_plan_rows"


def test_the_gate_never_concedes_while_evidence_is_missing():
    """A blocking-evidence submission is a certain attestation failure, not a
    partial score: the stall count is journaled but never concedes."""
    from gt_engine.persistent_plan.gate import MAX_REFUSALS_WITHOUT_PROGRESS

    decision = decide(
        plan=_plan(), unmet_rows=("req-a",), regressions=(),
        refusals=MAX_REFUSALS_WITHOUT_PROGRESS,
        refusals_without_progress=MAX_REFUSALS_WITHOUT_PROGRESS, **AMPLE
    )
    assert not decision.accepted
    assert decision.reason == "unmet_plan_rows"
    assert decision.escaped == ""
    evidence = decision.as_row()["evidence"]
    assert evidence["stalled_refusals"] == MAX_REFUSALS_WITHOUT_PROGRESS


def test_progress_earns_another_refusal():
    """An agent that keeps proving rows is never cut off."""
    from gt_engine.persistent_plan.gate import MAX_REFUSALS_WITHOUT_PROGRESS

    decision = decide(
        plan=_plan(), unmet_rows=("req-a",), regressions=(),
        refusals=MAX_REFUSALS_WITHOUT_PROGRESS + 5,
        refusals_without_progress=0, **AMPLE
    )
    assert not decision.accepted


def test_a_caller_that_omits_the_stall_counter_still_refuses():
    """Backward compatible: the counter argument is still accepted, but the
    count no longer buys an accept."""
    from gt_engine.persistent_plan.gate import MAX_REFUSALS_WITHOUT_PROGRESS

    decision = decide(
        plan=_plan(), unmet_rows=("req-a",), regressions=(),
        refusals=MAX_REFUSALS_WITHOUT_PROGRESS, **AMPLE
    )
    assert not decision.accepted
    assert decision.reason == "unmet_plan_rows"


def test_low_time_refuses_and_journals_the_escape_that_was_available():
    """A blocking-evidence submit inside the reserve still refuses; the
    journaled evidence records that a budget escape would have applied."""
    decision = decide(
        plan=_plan(), unmet_rows=("req-a",), regressions=(), refusals=0,
        remaining_seconds=MIN_REMAINING_SECONDS - 1, remaining_steps=200,
    )
    assert not decision.accepted
    assert decision.reason == "unmet_plan_rows"
    assert decision.escaped == ""
    evidence = decision.as_row()["evidence"]
    assert evidence["budget_allows_refusal"] is False
    assert evidence["escape_available"] == "time"


def test_low_steps_refuses_and_journals_the_escape_that_was_available():
    decision = decide(
        plan=_plan(), unmet_rows=("req-a",), regressions=(), refusals=0,
        remaining_seconds=3000.0, remaining_steps=MIN_REMAINING_STEPS - 1,
    )
    assert not decision.accepted
    evidence = decision.as_row()["evidence"]
    assert evidence["budget_allows_refusal"] is False
    assert evidence["escape_available"] == "steps"


def test_a_baseline_regression_alone_refuses():
    decision = decide(
        plan=_plan(), unmet_rows=(), regressions=("tests/test_x.py::test_y",),
        refusals=0, **AMPLE,
    )
    assert not decision.accepted
    assert decision.reason == "baseline_regression"
    assert "tests/test_x.py::test_y" in decision.directive
    assert "green when you arrived" in decision.directive


def test_no_plan_never_gates():
    decision = decide(
        plan=None, unmet_rows=("req-a",), regressions=(), refusals=0, **AMPLE
    )
    assert decision.accepted
    assert decision.reason == "no_plan"


def test_an_empty_plan_never_gates():
    empty = _plan(rows=())
    decision = decide(
        plan=empty, unmet_rows=("req-a",), regressions=(), refusals=0, **AMPLE
    )
    assert decision.accepted
    assert decision.reason == "no_plan"


def test_no_plan_with_unresolved_predicates_still_refuses():
    """A missing plan is not missing evidence.

    Run 35168421439 (cyclotruc): the plan bootstrap died on a provider
    timeout (``persistent_plan_unavailable``), the gate's plan early-return
    then short-circuited before the unresolved-predicate channel was ever
    read, and the submit shipped over 3 live RED predicates. ``no_plan``
    means "no row census" -- it must never mean "no gate".
    """
    decision = decide(
        plan=None, unmet_rows=(), regressions=(), refusals=0,
        unresolved_predicates=("pred-red-1",), **AMPLE,
    )
    assert not decision.accepted
    assert decision.reason == "unresolved_predicates"
    assert decision.unresolved_predicates == ("pred-red-1",)
    assert "pred-red-1" in decision.directive


def test_an_empty_plan_with_unresolved_predicates_still_refuses():
    empty = _plan(rows=())
    decision = decide(
        plan=empty, unmet_rows=(), regressions=(), refusals=0,
        unresolved_predicates=("pred-red-1",), **AMPLE,
    )
    assert not decision.accepted
    assert decision.reason == "unresolved_predicates"


@pytest.mark.parametrize(
    "seconds,steps,allowed",
    [
        (3000.0, 200, True),
        (MIN_REMAINING_SECONDS, MIN_REMAINING_STEPS, True),
        (MIN_REMAINING_SECONDS - 0.1, 200, False),
        (3000.0, MIN_REMAINING_STEPS - 1, False),
        (0.0, 0, False),
    ],
)
def test_budget_predicate(seconds, steps, allowed):
    assert budget_allows_refusal(seconds, steps)[0] is allowed


def test_the_directive_describes_actual_refusal_policy():
    text = render_directive(_plan(), ("req-a",), ())
    assert "reassessed" in text
    assert "accepted either way" not in text
    assert "bounded stall limit" not in text
    assert "will not be executed while" in text
    assert "disagree" in text


def test_the_directive_truncates_a_long_unmet_list():
    plan = _plan(rows=tuple(f"req-{index}" for index in range(30)))
    text = render_directive(plan, tuple(f"req-{index}" for index in range(30)), ())
    assert "and 18 more" in text


def test_the_decision_row_is_journal_shaped():
    decision = decide(
        plan=_plan(), unmet_rows=("req-a",), regressions=(), refusals=0, **AMPLE
    )
    row = decision.as_row()
    assert row["accepted"] is False
    assert row["unmet_rows"] == ["req-a"]
    assert isinstance(row["remaining_seconds"], float)
    assert isinstance(row["remaining_steps"], int)


def test_an_unmapped_red_predicate_alone_refuses():
    """A RED predicate bound to no plan row is blocking evidence the row
    census cannot see: ``evaluate_failing_observation`` reddens any matched
    contract obligation and ``_link_obligations`` never promised every
    obligation a row. It refuses under its own reason so an audit can tell
    an unmapped-predicate refusal from an unmet-row one."""
    decision = decide(
        plan=_plan(), unmet_rows=(), regressions=(), refusals=0,
        unresolved_predicates=("pred-obl-orphan",),
        predicate_labels={"pred-obl-orphan": "the tokenizer handles orphan tokens"},
        **AMPLE,
    )
    assert not decision.accepted
    assert decision.reason == "unresolved_predicates"
    row = decision.as_row()
    assert row["unmet_rows"] == []
    assert row["unresolved_predicates"] == ["pred-obl-orphan"]
    assert row["evidence"]["unresolved_predicates"] == ["pred-obl-orphan"]
    assert row["completion_proven"] is False
    # The directive must name the obligation, not the hash - a bare
    # ``pred-<hash>`` refusal is unactionable.
    assert "the tokenizer handles orphan tokens" in decision.directive
    assert "pred-obl-orphan" not in decision.directive


def test_unresolved_predicates_disprove_completion_even_with_verified_rows():
    """Same shape as a regression: current failing evidence against an
    obligation the plan never tracked means completion was never proven,
    even when every row's check passed."""
    decision = decide(
        plan=_plan(), unmet_rows=(), regressions=(), refusals=0,
        baseline_status="intact",
        row_states={"req-a": "CHECK_PASSED", "req-b": "CHECK_PASSED"},
        unresolved_predicates=("pred-obl-orphan",),
        **AMPLE,
    )
    assert not decision.accepted
    assert decision.as_row()["completion_proven"] is False


def test_row_unmet_reason_still_wins_over_unresolved_predicates():
    """When both channels have evidence the row reason leads; the
    unresolved predicates are still journaled beside it."""
    decision = decide(
        plan=_plan(), unmet_rows=("req-a",), regressions=(), refusals=0,
        unresolved_predicates=("pred-obl-orphan",), **AMPLE,
    )
    assert not decision.accepted
    assert decision.reason == "unmet_plan_rows"
    assert decision.as_row()["unresolved_predicates"] == ["pred-obl-orphan"]


def test_the_directive_falls_back_to_the_predicate_id_without_a_label():
    text = render_directive(_plan(), (), (), ("pred-obl-orphan",))
    assert "pred-obl-orphan" in text
