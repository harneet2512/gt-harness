"""REV-374: a predicate or verdict that declines must name why, and still block.

Returning False for "I did not recognise this run" is indistinguishable from
"I audited it and it violated". These are the negative cases: drive a shape
mismatch and assert both that an entry is recorded and that acceptance is
withheld.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.gt_installed_rehearsal import (  # noqa: E402
    accepts_synthetic_repair,
    shape_mismatch,
    unaudited_reason,
)


def test_matching_shape_returns_none() -> None:
    """The auditor must not cry mismatch on a run it can audit."""
    assert shape_mismatch("unaudited_run_shape", "p", checks=(2, 2)) is None
    assert shape_mismatch("k", "p", a=(1, 1), b=(8, 8)) is None


def test_mismatch_names_predicate_reason_and_both_values() -> None:
    entry = shape_mismatch(
        "unaudited_run_shape", "execution_evidence_verified",
        checks=(1, 2), agent_requests=(9, 8),
    )
    assert entry is not None
    assert entry["predicate"] == "execution_evidence_verified"
    assert entry["reason"] == "unaudited_run_shape"
    # Observed AND expected, so a reader never has to infer the expectation.
    assert entry["checks"] == 1 and entry["expected_checks"] == 2
    assert entry["agent_requests"] == 9 and entry["expected_agent_requests"] == 8


def test_single_field_mismatch_is_enough() -> None:
    """One wrong field must not be masked by other fields agreeing."""
    entry = shape_mismatch("k", "p", ok=(3, 3), bad=(7, 8))
    assert entry is not None and entry["bad"] == 7 and entry["expected_bad"] == 8


def test_bootstrap_shift_is_the_real_regression_case() -> None:
    """The exact 24b shape: one check, an extra provider request.

    This is what silently switched execution_evidence_verified off before F11.
    """
    entry = shape_mismatch(
        "unaudited_run_shape", "execution_evidence_verified",
        checks=(1, 2), agent_requests=(7, 8),
    )
    assert entry is not None
    assert entry["reason"] == "unaudited_run_shape"


def test_verdict_shape_mismatch_is_reported_like_a_predicate() -> None:
    """The verdict gate uses the same entry shape, so one reader handles both."""
    entry = shape_mismatch("acceptance_shape_mismatch", "status",
                           commands=(9, 8), audits=(1, 1))
    assert entry is not None
    assert entry["predicate"] == "status"
    assert entry["reason"] == "acceptance_shape_mismatch"
    assert entry["commands"] == 9 and entry["expected_commands"] == 8


class _Audit:
    verdict = "GREEN-delivered"
    synthetic_transport = True


def _accepting_receipt() -> dict[str, object]:
    return {
        "verifier_patch_matches": True,
        "predicates_not_evaluated": [],
        "reproduction_verified": True,
        "execution_evidence_verified": True,
        "native_graph_refresh_verified": True,
        "pre_repair_source_stable": True,
        "runtime_receipt_errors": ["synthetic_transport_not_paid_evidence"],
    }


def _gate(receipt: dict[str, object], **over: object) -> bool:
    kwargs: dict[str, object] = {
        "exception_info": None,
        "rewards": {"reward": 1},
        "command_count": 8,
        "audits": [_Audit()],
        "exit_status": "Submitted",
    }
    kwargs.update(over)
    return accepts_synthetic_repair(receipt, **kwargs)  # type: ignore[arg-type]


def test_gate_accepts_a_clean_run() -> None:
    """Guards the negatives: if this ever fails, the blockers prove nothing."""
    assert _gate(_accepting_receipt()) is True


@pytest.mark.parametrize("blocker", ["predicates_not_evaluated", "acceptance_shape_mismatch"])
def test_declining_receipt_withholds_acceptance(blocker: str) -> None:
    """REV-375: drives the REAL gate, not a restatement of it.

    A recorded reason that still permitted acceptance would leave the original
    hole with better documentation.
    """
    receipt = _accepting_receipt()
    receipt[blocker] = (
        [shape_mismatch("k", "p", a=(1, 2))] if blocker == "predicates_not_evaluated"
        else shape_mismatch("acceptance_shape_mismatch", "status", commands=(9, 8))
    )
    assert _gate(receipt) is False


def test_gate_blocks_on_scenario_drift() -> None:
    """The literal 8 is the scenario definition; drift must not be accepted."""
    assert _gate(_accepting_receipt(), command_count=9) is False


def test_gate_blocks_on_unexpected_receipt_error() -> None:
    receipt = _accepting_receipt()
    receipt["runtime_receipt_errors"] = [
        "synthetic_transport_not_paid_evidence", "treatment_provider_receipts_invalid",
    ]
    assert _gate(receipt) is False


# 6:52 item 1 - the noise half. An interrupted run declines these predicates
# by construction, and saying "unaudited_run_shape" there reports an intended
# ending as an unrecognised one. Measured on the real transport with
# interrupt_at_ordinal=6: 6 commands served, 1 execution_evidence check,
# 7 agent requests. These drive unaudited_reason, the production authority.


def test_interrupted_run_missing_its_second_check_is_not_an_unknown_shape() -> None:
    """The measured interrupted shape: checks == 1."""
    assert unaudited_reason(forced_interruption=True, checks=1) == (
        "not_applicable_interrupted"
    )


def test_interrupted_run_with_no_checks_is_still_the_interruption() -> None:
    assert unaudited_reason(forced_interruption=True, checks=0) == (
        "not_applicable_interrupted"
    )


def test_interrupted_run_that_produced_both_checks_stays_loud() -> None:
    """An interruption cannot reach the second check; if it did, that is real."""
    assert unaudited_reason(forced_interruption=True, checks=2) == "unaudited_run_shape"


@pytest.mark.parametrize("checks", [0, 1, 2, 3])
def test_uninterrupted_run_never_borrows_the_interruption_reason(checks: int) -> None:
    """The excuse is available only to runs that were actually interrupted."""
    assert unaudited_reason(forced_interruption=False, checks=checks) == (
        "unaudited_run_shape"
    )


def test_interruption_reason_reaches_the_recorded_entry() -> None:
    """The reason is what a reader of predicates_not_evaluated actually sees."""
    entry = shape_mismatch(
        unaudited_reason(forced_interruption=True, checks=1),
        "execution_evidence_verified",
        checks=(1, 2),
        agent_requests=(7, 6),
    )
    assert entry is not None
    assert entry["reason"] == "not_applicable_interrupted"
    assert entry["checks"] == 1 and entry["expected_checks"] == 2


def test_declining_still_withholds_acceptance_when_reason_is_interruption() -> None:
    """A softer reason must not soften the gate: it is diagnostic only."""
    receipt = _accepting_receipt()
    receipt["predicates_not_evaluated"] = [
        shape_mismatch("not_applicable_interrupted", "execution_evidence_verified",
                       checks=(1, 2)),
    ]
    assert _gate(receipt) is False
