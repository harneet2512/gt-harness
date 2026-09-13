"""The run-loop half of the transition study, which the producer study cannot measure.

`scripts/graph_transition_study.py` measures the PRODUCER: what one transition
costs to build, both ways. Its own docstring says what it deliberately does not
cover -- "the harness's blocked-versus-background split, the checks it runs and
the snapshots it takes are properties of the coordinator and the run loop, not of
the producer, and they need their own measurement against the live path". This is
that measurement, and it reads a run's own journal rather than instrumenting
anything, so it applies to runs already on disk.

Three things it has to get right, each of which is a way the number could lie:

BLOCKED TIME IS MEASURED, NOT ASSUMED. The build coordinator is gone: amends
run inside the edit boundary (`graph_sync_amend`) or at a serving boundary
(`graph_boundary_amend`, `graph_recovery`) and carry `elapsed_ms`. Their sum
is what graph work costs the agent's clock.

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


def test_blocked_time_is_the_sum_of_boundary_amends():
    rows = [
        _row("graph_publication", 0),
        _row("graph_sync_amend", 5, elapsed_ms=1200),
        _row("graph_boundary_amend", 9, elapsed_ms=3400),
        _row("session_closed", 20),
    ]
    result = account_run_loop(rows)
    assert result["blocked_seconds"] == 4.6
    assert result["builds"] == 2
    assert "elapsed_ms" in result["blocked_basis"]


def test_blocked_time_carries_the_reason_it_is_zero():
    """A bare 0.0 reads as "graph builds are free". It is not why."""
    result = account_run_loop([_row("session_closed", 1)])
    assert result["blocked_seconds"] == 0.0
    assert "elapsed_ms" in result["blocked_basis"]


def test_no_worker_exists_to_wait_for():
    """The coordinator is deleted, not merely unwired: if a wait primitive
    ever reappears, blocked_seconds silently becomes a lie."""
    import inspect

    from gt_engine import graph_coordinator, miniswe_integration, miniswe_runtime

    for module in (graph_coordinator, miniswe_integration, miniswe_runtime):
        assert "wait_idle" not in inspect.getsource(module), module.__name__
    assert not hasattr(graph_coordinator, "GraphBuildCoordinator")


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


def test_a_verified_no_change_transaction_is_not_an_invalidation():
    """Automatic checks post `edit_transaction` even when they changed nothing.

    drain_plan_checks and _plan_baseline_check journal the row unconditionally
    in a finally block. A complete empty diff attests the tree did not move --
    the adopted graph still describes it -- so treating the row as an
    invalidation opens a dark interval no publication can ever close (the
    emitter dedupes on unchanged manifest+revision). That was the
    `ended_graph_dark` tail in run 34754150319.
    """
    rows = [
        _row("graph_publication", 0),
        _row("edit_transaction", 3, changed_paths=[], complete=True,
             omissions=[], pre_revision="rev0", post_revision="rev0"),
        _row("edit_transaction", 5, changed_paths=["service.py"],
             complete=True, omissions=[], pre_revision="rev0",
             post_revision="rev1"),
        _row("graph_publication", 9),
        _row("session_closed", 10),
    ]
    result = account_run_loop(rows)
    assert result["invalidations"] == 1
    assert result["graph_dark_seconds"] == 4.0
    assert result["graph_dark_intervals"] == [
        {"start": 5.0, "end": 9.0, "closed": True}]
    assert result["ended_graph_dark"] is False


def test_an_incomplete_transaction_still_invalidates_without_changed_paths():
    """Unenumerated change is a real invalidation even with zero named paths."""
    rows = [
        _row("graph_publication", 0),
        _row("edit_transaction", 3, changed_paths=[], complete=False,
             omissions=["capture_incomplete"]),
        _row("graph_publication", 9),
        _row("session_closed", 10),
    ]
    result = account_run_loop(rows)
    assert result["invalidations"] == 1
    assert result["graph_dark_seconds"] == 6.0


def test_a_revision_advance_without_enumerated_paths_still_invalidates():
    """An empty diff whose revision still moved is unenumerated change, not a
    verified no-change boundary -- e.g. git state the file diff cannot see."""
    rows = [
        _row("graph_publication", 0),
        _row("edit_transaction", 3, changed_paths=[], complete=True,
             omissions=[], pre_revision="rev0", post_revision="rev1"),
        _row("graph_publication", 9),
        _row("session_closed", 10),
    ]
    result = account_run_loop(rows)
    assert result["invalidations"] == 1
    assert result["graph_dark_seconds"] == 6.0
