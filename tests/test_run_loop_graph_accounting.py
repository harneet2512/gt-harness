"""The run-loop half of the transition study, which the producer study cannot measure.

`scripts/graph_transition_study.py` measures the PRODUCER: what one transition
costs to build, both ways. Its own docstring says what it deliberately does not
cover -- "the harness's blocked-versus-background split, the checks it runs and
the snapshots it takes are properties of the coordinator and the run loop, not of
the producer, and they need their own measurement against the live path". This is
that measurement, and it reads a run's own journal rather than instrumenting
anything, so it applies to runs already on disk.

Three things it has to get right, each of which is a way the number could lie:

BLOCKED TIME IS ZERO BY CONSTRUCTION, and that is a finding, not an omission.
`GraphBuildCoordinator.wait_idle` exists and NOTHING on the live path calls it;
`close_graph_coordinator` closes with `wait=False` on purpose, because an
uncooperative in-flight pass otherwise holds the process past its deadline and
the supervisor turns a scored submission into an infra timeout. So the run never
waits for a graph. Reporting "blocked: 0.0s" without saying why invites the
reading that graph builds are free.

WHAT THEY ACTUALLY COST IS GRAPH-DARK TIME. Between the edit that invalidates the
graph and the publication that adopts the rebuild, every caller query, anchor and
covering-test selection abstains. That interval is the run-loop cost of a
transition, and it is what a reader wants when they ask what the graph costs.

AN UNCLOSED INTERVAL IS NOT ZERO. A run that ends while still stale -- which is
rehearsal 06 and 07's shape, where the rebuild lands after the last action -- has
a graph-dark interval that never closes. Dropping it would report the runs with
the WORST staleness as having the least, which is exactly backwards.
"""
from __future__ import annotations

import pytest

from scripts.run_loop_graph_accounting import account_run_loop


def _row(event: str, second: int, **fields) -> dict:
    # Rolls into minutes: `16:00:80` is not a time, and an unparseable stamp is
    # DROPPED by the accounting, so a naive helper silently shortens the run it
    # is describing rather than failing.
    minute, second = divmod(second, 60)
    return {"event": event,
            "timestamp_utc": f"2026-09-10T16:{minute:02d}:{second:02d}+00:00",
            **fields}


def test_a_run_that_never_invalidates_its_graph_is_never_dark():
    rows = [
        _row("repository_snapshot", 0, boundary="task_start"),
        _row("graph_publication", 1),
        _row("execution_started", 2),
        _row("execution_finished", 3),
        _row("session_closed", 4),
    ]
    result = account_run_loop(rows)
    assert result["graph_dark_seconds"] == 0.0
    assert result["graph_dark_intervals"] == []
    assert result["blocked_seconds"] == 0.0


def test_the_interval_between_invalidation_and_publication_is_the_cost():
    rows = [
        _row("graph_publication", 0),
        _row("graph_invalidated", 10, paths=["a.py"]),
        _row("graph_build_mode", 14, amended=[{"time_ms": 3000}]),
        _row("graph_publication", 18),
        _row("session_closed", 20),
    ]
    result = account_run_loop(rows)
    assert result["graph_dark_seconds"] == 8.0
    assert result["graph_dark_intervals"] == [{"start": 10.0, "end": 18.0, "closed": True}]
    # The build ran inside that window and did not block the run.
    assert result["background_build_seconds"] == 3.0
    assert result["blocked_seconds"] == 0.0


def test_a_run_that_ends_while_stale_still_reports_the_interval():
    """Rehearsal 06 and 07's shape. Dropping this inverts the ranking."""
    rows = [
        _row("graph_publication", 0),
        _row("graph_invalidated", 5, paths=["a.py"]),
        _row("execution_started", 7),
        _row("session_closed", 25),
    ]
    result = account_run_loop(rows)
    assert result["graph_dark_seconds"] == 20.0
    assert result["graph_dark_intervals"] == [{"start": 5.0, "end": 25.0, "closed": False}]
    assert result["ended_graph_dark"] is True


def test_repeated_invalidations_inside_one_dark_window_do_not_double_count():
    """An agent editing continuously invalidates many times before one publish."""
    rows = [
        _row("graph_publication", 0),
        _row("graph_invalidated", 4, paths=["a.py"]),
        _row("graph_invalidated", 6, paths=["b.py"]),
        _row("graph_invalidated", 8, paths=["c.py"]),
        _row("graph_publication", 12),
        _row("session_closed", 13),
    ]
    result = account_run_loop(rows)
    assert result["graph_dark_seconds"] == 8.0
    assert len(result["graph_dark_intervals"]) == 1
    assert result["invalidations"] == 3


def test_checks_and_snapshots_are_counted_as_the_item_asks():
    rows = [
        _row("repository_snapshot", 0, boundary="task_start"),
        _row("repository_snapshot", 1, boundary="before_action"),
        _row("repository_snapshot", 2, boundary="after_action"),
        _row("execution_started", 3),
        _row("execution_finished", 4),
        _row("execution_evidence", 5, outcome="fail"),
        _row("execution_started", 6),
        _row("execution_finished", 7),
        _row("execution_evidence", 8, outcome="pass"),
        _row("session_closed", 9),
    ]
    result = account_run_loop(rows)
    assert result["executions"] == 2
    assert result["checks"] == 2
    assert result["check_outcomes"] == {"fail": 1, "pass": 1}
    assert result["snapshots"] == 3
    assert result["snapshots_by_boundary"] == {
        "task_start": 1, "before_action": 1, "after_action": 1}


def test_the_dark_fraction_is_of_the_run_the_journal_describes():
    rows = [
        _row("graph_publication", 0),
        _row("graph_invalidated", 20, paths=["a.py"]),
        _row("graph_publication", 40),
        _row("session_closed", 80),
    ]
    result = account_run_loop(rows)
    assert result["wall_seconds"] == 80.0
    assert result["graph_dark_seconds"] == 20.0
    assert result["graph_dark_fraction"] == 0.25


def test_an_empty_journal_reports_nothing_rather_than_zero_cost():
    """No rows is not a run with a perfectly fresh graph."""
    result = account_run_loop([])
    assert result["status"] == "unmeasured"
    assert result["graph_dark_seconds"] is None


def test_blocked_time_carries_the_reason_it_is_zero():
    """A bare 0.0 reads as "graph builds are free". It is not why."""
    result = account_run_loop([_row("session_closed", 1)])
    assert result["blocked_seconds"] == 0.0
    assert "wait_idle" in result["blocked_basis"]


def test_nothing_on_the_live_path_waits_for_a_graph_build():
    """The claim above, checked against the source rather than restated.

    If a caller ever appears, `blocked_seconds` silently becomes a lie, and it
    is the kind of lie nobody re-derives -- a zero looks like a measurement.
    """
    import inspect

    from gt_engine import graph_coordinator, miniswe_integration, miniswe_runtime

    for module in (miniswe_integration, miniswe_runtime):
        assert "wait_idle" not in inspect.getsource(module), module.__name__
    # It still has to EXIST, or the accounting is describing a coordinator that
    # is not the one shipped.
    assert hasattr(graph_coordinator.GraphBuildCoordinator, "wait_idle")


@pytest.mark.parametrize("event", ["graph_invalidated", "edit_transaction"])
def test_either_invalidation_signal_opens_the_window(event):
    """`graph_invalidated` is the direct signal; an edit is the fallback.

    A journal from before the invalidation row existed still has to account,
    or the instrument cannot read the runs already on disk.
    """
    rows = [
        _row("graph_publication", 0),
        _row(event, 3),
        _row("graph_publication", 9),
        _row("session_closed", 10),
    ]
    assert account_run_loop(rows)["graph_dark_seconds"] == 6.0
