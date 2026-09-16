"""Fleet-gate coverage for scripts/lsp_watch.py.

The strict gate is the standing regression check for the seal-freeze failure
class: any sealed task that froze a partial tier, dropped a scheduled leg,
broke the producer pipeline, or sealed on a leg-demanding graph without
convergence must fail the cohort scan - the 34904339448 verdict shape must
never again need a paid postmortem to be visible.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from lsp_watch import TaskHealth  # noqa: E402


def _health(rows: list[dict]) -> TaskHealth:
    health = TaskHealth("t")
    for row in rows:
        health.feed(row)
    return health


def _pub(sha: str) -> dict:
    return {"event": "graph_publication", "graph_sha256": sha}


def _leg(graph: str) -> dict:
    return {"event": "lsp_promotion_scheduled", "graph_revision": graph}


def _term(graph: str, disposition: str = "published") -> dict:
    return {
        "event": "lsp_promotion_terminal",
        "input_graph_revision": graph,
        "disposition": disposition,
    }


_SEALED = {"event": "session_closed"}


def test_clean_task_passes_the_gate() -> None:
    health = _health([
        _pub("g1"), _leg("g1"), _term("g1"),
        _pub("g2"), _leg("g2"), _term("g2"), _SEALED,
    ])
    assert health.fail_flags() == []


def test_partial_salvage_frozen_at_seal_fails() -> None:
    health = _health([
        _pub("g1"), _leg("g1"), _term("g1", "obsolete"),
        {"event": "lsp_salvage", "graph_revision": "g2", "outcome": "published",
         "applied": 13, "skipped_diverged": 10, "skipped_stale": 0},
        _pub("g2"), _SEALED,
    ])
    fails = health.fail_flags()
    assert any(f.startswith("TIER_PARTIAL") for f in fails)


def test_leg_demanding_graph_sealed_without_leg_fails() -> None:
    health = _health([
        _pub("g1"), _leg("g1"), _term("g1"),
        _pub("g2"), _SEALED,
    ])
    fails = health.fail_flags()
    assert any(f.startswith("NO_LEG_ON_FINAL") for f in fails)


def test_seal_convergence_terminated_clears_the_flag() -> None:
    health = _health([
        _pub("g1"), _leg("g1"), _term("g1"),
        _pub("g2"), _leg("g2"), _term("g2", "obsolete"),
        {"event": "lsp_seal_convergence", "outcome": "terminated",
         "graph_revision": "g2"},
        _term("g2"),
        _SEALED,
    ])
    assert health.fail_flags() == []


def test_seal_window_lapse_still_fails() -> None:
    health = _health([
        _pub("g1"), _leg("g1"), _term("g1"),
        _pub("g2"), _leg("g2"),
        {"event": "lsp_seal_convergence", "outcome": "timeout",
         "graph_revision": "g2"},
        _SEALED,
    ])
    fails = health.fail_flags()
    assert any("seal=timeout" in f for f in fails)


def test_scheduled_leg_without_terminal_fails() -> None:
    health = _health([
        _pub("g1"), _leg("g1"), _leg("g1"), _term("g1"),
        _SEALED,
    ])
    fails = health.fail_flags()
    assert any(f.startswith("SCHEDULED_NO_TERMINAL") for f in fails)


def test_amend_failure_fails() -> None:
    health = _health([
        _pub("g1"), _leg("g1"), _term("g1"),
        {"event": "graph_refresh", "reason": "amend_failed:exit=1"},
        _SEALED,
    ])
    fails = health.fail_flags()
    assert any(f.startswith("AMEND_FAILURES") for f in fails)


def test_no_demand_task_passes() -> None:
    health = _health([_pub("g1"), _SEALED])
    assert health.fail_flags() == []


def test_unavailable_event_with_exception_reason_fails() -> None:
    """Gate-one sealed with nine of these and the strict gate read clean."""
    health = _health([
        _pub("g1"),
        _leg("g1"),
        _term("g1"),
        {"event": "semantic_localization_unavailable",
         "reason": "TypeError:float() argument must be a string or a real "
                   "number, not 'NoneType'"},
        {"event": "session_closed"},
    ])
    fails = health.fail_flags()
    assert any(f.startswith("CAPABILITY_CRASH(1)") for f in fails)


def test_unavailable_event_with_typed_reason_is_not_a_crash() -> None:
    """Designed abstention stays graceful - only exception classes fail."""
    health = _health([
        _pub("g1"),
        _leg("g1"),
        _term("g1"),
        {"event": "semantic_localization_unavailable",
         "reason": "graph_snapshot_not_current"},
        {"event": "dense_index_ready", "reason": "GT_INDEX_PENDING"},
        {"event": "session_closed"},
    ])
    assert health.capability_crashes == 0
    assert health.fail_flags() == []
