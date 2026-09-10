"""What a graph transition costs the RUN, as opposed to the producer.

`graph_transition_study.py` measures the producer: what one transition costs to
build, both ways. Its docstring is explicit that it does not cover "the harness's
blocked-versus-background split, the checks it runs and the snapshots it takes",
because those are properties of the coordinator and the run loop. This is that
half.

It reads a run's own event journal and computes nothing the run did not already
record, which matters twice: it applies to runs already on disk, and it cannot
perturb the timings it is reporting.

THE BLOCKED/BACKGROUND SPLIT HAS A DEGENERATE ANSWER, and saying so plainly is
the point. `GraphBuildCoordinator.wait_idle` exists and NOTHING on the live path
calls it. `close_graph_coordinator` closes with `wait=False` deliberately: an
uncooperative in-flight pass otherwise holds the process past its deadline and
the supervisor turns a scored submission into an infra timeout. So the run never
waits for a graph build, and blocked time is zero by construction rather than by
measurement. Reported with its reason attached, because a bare `0.0` invites the
reading that graph builds are free.

THEY ARE NOT FREE. What a transition costs the run is GRAPH-DARK TIME: the
interval between the edit that invalidates the graph and the publication that
adopts the rebuild. Inside it every caller query, anchor and covering-test
selection abstains -- this is the interval that, on arktype, never closed at all
because a ~115s build lost to a ~50s edit interval, and every read after the
first edit reported caller coverage unavailable.

AN UNCLOSED INTERVAL IS NOT ZERO. A run that ends while still stale has a window
that never closes, and dropping it would report the runs with the WORST staleness
as having the least. It is charged to the end of the journal and marked
`closed: false`.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

# The graph is stale from here until the next publication. `graph_invalidated` is
# the direct signal; `edit_transaction` is the fallback for journals written
# before it existed, since the instrument has to read runs already on disk.
INVALIDATION_EVENTS = ("graph_invalidated", "edit_transaction")

BLOCKED_BASIS = (
    "zero by construction: GraphBuildCoordinator.wait_idle has no caller on the "
    "live path and close_graph_coordinator closes with wait=False, so the run "
    "never waits for a graph build"
)


def _seconds(row: Mapping[str, Any]) -> float | None:
    stamp = str(row.get("timestamp_utc") or "")
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp).timestamp()
    except ValueError:
        return None


def _build_seconds(row: Mapping[str, Any]) -> float:
    """Producer-reported build duration, from the row the worker already writes."""
    total = 0.0
    for entry in row.get("amended") or ():
        if isinstance(entry, Mapping):
            total += float(entry.get("time_ms") or 0) / 1000.0
    return total


def account_run_loop(rows: Sequence[Mapping[str, Any]]) -> dict:
    """Graph-dark time, background build time, checks and snapshots for one run."""
    stamped = [(row, _seconds(row)) for row in rows]
    times = [t for _, t in stamped if t is not None]
    if not times:
        return {
            "schema": "gt.run_loop_graph_accounting.v1",
            "status": "unmeasured",
            "graph_dark_seconds": None,
            "blocked_seconds": 0.0,
            "blocked_basis": BLOCKED_BASIS,
        }

    started, ended = min(times), max(times)
    intervals: list[dict] = []
    dark_since: float | None = None
    invalidations = 0
    publications = 0
    builds = 0
    background = 0.0
    executions = 0
    checks = 0
    outcomes: dict[str, int] = {}
    snapshots = 0
    boundaries: dict[str, int] = {}
    dispositions: dict[str, int] = {}

    for row, when in stamped:
        event = str(row.get("event") or "")
        if event in INVALIDATION_EVENTS:
            invalidations += 1
            # Repeated invalidations inside one window are one window: the agent
            # editing continuously invalidates many times before one publish.
            if dark_since is None and when is not None:
                dark_since = when
        elif event == "graph_publication":
            publications += 1
            if dark_since is not None and when is not None:
                intervals.append({"start": dark_since, "end": when, "closed": True})
                dark_since = None
        elif event == "graph_build_mode":
            builds += 1
            background += _build_seconds(row)
        elif event == "graph_refresh_scheduled":
            key = str(row.get("disposition") or "unknown")
            dispositions[key] = dispositions.get(key, 0) + 1
        elif event == "execution_started":
            executions += 1
        elif event == "execution_evidence":
            checks += 1
            outcome = str(row.get("outcome") or "unknown")
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
        elif event == "repository_snapshot":
            snapshots += 1
            boundary = str(row.get("boundary") or "unknown")
            boundaries[boundary] = boundaries.get(boundary, 0) + 1

    ended_dark = dark_since is not None
    if dark_since is not None:
        intervals.append({"start": dark_since, "end": ended, "closed": False})

    origin = started
    for interval in intervals:
        interval["start"] = round(interval["start"] - origin, 3)
        interval["end"] = round(interval["end"] - origin, 3)
    dark = round(sum(i["end"] - i["start"] for i in intervals), 3)
    wall = round(ended - started, 3)

    return {
        "schema": "gt.run_loop_graph_accounting.v1",
        "status": "measured",
        "wall_seconds": wall,
        # Zero, and the reason travels with it.
        "blocked_seconds": 0.0,
        "blocked_basis": BLOCKED_BASIS,
        "background_build_seconds": round(background, 3),
        "graph_dark_seconds": dark,
        "graph_dark_fraction": round(dark / wall, 4) if wall else 0.0,
        "graph_dark_intervals": intervals,
        "ended_graph_dark": ended_dark,
        "invalidations": invalidations,
        "publications": publications,
        "builds": builds,
        "refresh_dispositions": dispositions,
        "executions": executions,
        "checks": checks,
        "check_outcomes": outcomes,
        "snapshots": snapshots,
        "snapshots_by_boundary": boundaries,
    }


def read_journal(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("journals", nargs="+", type=Path,
                        help="events.jsonl files, one per run")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    report = {"schema": "gt.run_loop_graph_accounting_report.v1", "runs": []}
    for journal in args.journals:
        row = account_run_loop(read_journal(journal))
        row["journal"] = str(journal)
        report["runs"].append(row)
    encoded = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        args.out.write_text(encoded, encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
