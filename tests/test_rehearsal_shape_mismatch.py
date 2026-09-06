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
from scripts.gt_installed_rehearsal import shape_mismatch  # noqa: E402


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


@pytest.mark.parametrize("blocker", ["predicates_not_evaluated", "acceptance_shape_mismatch"])
def test_declining_receipt_withholds_acceptance(blocker: str) -> None:
    """Both blockers must withhold VERIFIED_SYNTHETIC_REPAIR.

    Mirrors the gate: acceptance requires every other condition AND an empty
    blocker. A recorded reason that still permitted acceptance would leave the
    original hole with better documentation.
    """
    receipt: dict[str, object] = {
        "verifier_patch_matches": True,
        "predicates_not_evaluated": [],
        "reproduction_verified": True,
        "execution_evidence_verified": True,
        "native_graph_refresh_verified": True,
        "pre_repair_source_stable": True,
    }

    def accepts(r: dict[str, object]) -> bool:
        return bool(
            r.get("verifier_patch_matches")
            and not r.get("predicates_not_evaluated")
            and not r.get("acceptance_shape_mismatch")
            and r.get("reproduction_verified")
            and r.get("execution_evidence_verified")
            and r.get("native_graph_refresh_verified")
            and r.get("pre_repair_source_stable")
        )

    assert accepts(receipt) is True
    receipt[blocker] = (
        [shape_mismatch("k", "p", a=(1, 2))] if blocker == "predicates_not_evaluated"
        else shape_mismatch("acceptance_shape_mismatch", "status", commands=(9, 8))
    )
    assert accepts(receipt) is False
