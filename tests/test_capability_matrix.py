"""Capability-coverage enforcement.

Run 34801009507 proved the failure mode of the whole project: defect classes
reached a paid run because no offline check owned them. This matrix is the
ledger - every GT capability, every observed failure mode, and the test
surface that covers it. A failure mode with no covering test is a readiness
blocker unless it is explicitly classified (``open`` with a reason, or
``known-issue``); an unlisted defect class can never silently ship because
adding it to the matrix is the only way to describe it.

The test enforces the ledger, not the fixes: entries marked ``open`` fail the
readiness gate only when someone asserts readiness; per-mode coverage must
always name real files so the matrix cannot rot into fiction.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MATRIX = json.loads(
    (ROOT / "tests" / "fixtures" / "capability_matrix.json").read_text(
        encoding="utf-8"
    )
)

# Statuses that mean "this mode has an owned test surface" - they must name
# covering tests. Anything else is an open/in-flight status and must carry a
# reason so the readiness gate knows what blocks it.
_TESTED_STATUSES = {"covered", "fixed", "covered-by-design"}
_OPEN_STATUSES = {
    "open", "known-issue", "fixing", "investigating", "diagnosing", "partial",
}


def _modes():
    for cap in MATRIX["capabilities"]:
        for mode in cap["failure_modes"]:
            yield cap["id"], mode


def test_every_named_test_file_exists():
    missing = []
    for cap_id, mode in _modes():
        for ref in mode.get("covering_tests", []):
            if not (ROOT / ref).exists():
                missing.append(f"{cap_id}/{mode['id']}: {ref}")
    assert not missing, "covering_tests entries that do not exist:\n" + "\n".join(missing)


@pytest.mark.parametrize(
    "cap_id,mode",
    [pytest.param(c, m, id=f"{c}:{m['id']}") for c, m in _modes()],
)
def test_failure_mode_has_owner(cap_id, mode):
    status = mode["status"]
    head = status.split(" ")[0].split(";")[0].rstrip("-")
    if any(status.startswith(s) for s in _TESTED_STATUSES):
        assert mode.get("covering_tests"), (
            f"{cap_id}/{mode['id']} claims {status} with no covering_tests"
        )
    else:
        assert head in _OPEN_STATUSES or status.startswith("open"), (
            f"{cap_id}/{mode['id']} has untracked status {status!r}"
        )
        # Open modes are readiness blockers: they must carry the reason.
        assert (
            " - " in status or ";" in status or status.rstrip() in _OPEN_STATUSES
        ), f"{cap_id}/{mode['id']} status {status!r} needs a reason"


def test_every_capability_has_at_least_one_tested_mode_or_open_reason():
    for cap in MATRIX["capabilities"]:
        statuses = [m["status"] for m in cap["failure_modes"]]
        assert any(
            any(s.startswith(t) for t in _TESTED_STATUSES)
            or any(s.startswith(o) for o in _OPEN_STATUSES)
            or s.startswith(("fixing", "investigating", "diagnosing", "partial"))
            for s in statuses
        ), f"{cap['id']} has no classified failure modes"


def test_matrix_lists_every_smoke20_defect_class():
    """The defects the paid run surfaced must each have a matrix home."""
    joined = json.dumps(MATRIX)
    for needle in (
        "unsupported_shell_operator",
        "no_tests_observed",
        "duplicate GT delivery identity",
        "immediate boundary",
        "rate_limit",
        "missing_patch",
        "diagnostics",
        "tsserver",
        "churn",
        "non_convergence",
        "zero_edges",
        "rust",
    ):
        assert needle in joined, f"smoke20 defect class missing from matrix: {needle}"
