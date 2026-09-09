"""The plan we can build with no model call at all.

This is the load-bearing half. Everything here is derived from facts already in
hand: the prompt's own lines, the graph's definitions and callers, the tests the
graph says cover those definitions, and the repository's own declared test
command. If a house is to be built, this is the part that says where it goes,
that it has four walls and a roof, and how you check it is standing. None of it
is a guess and none of it needs a provider.

The planning call is an ENRICHMENT on top of this, not a replacement for it. It
decides the things only judgement can decide -- which existing modes a
requirement must behave correctly under, what behaviour that implies, and which
specific command proves a particular row. When that call fails, truncates, or
returns something unusable, the deterministic plan still stands and the agent
still gets its requirements, its anchors, its blast radius and its checks.

That ordering is the lesson of the first production run: the call was truncated,
the plan was therefore empty, and a run that had already paid for a complete set
of graph facts delivered none of them.
"""
from __future__ import annotations

from . import STATUS_PARTIAL, STATUS_READY, PersistentPlan, PlanInputs, PlanRow

# A check is only useful if the agent can run it. More than a handful of test
# files per requirement stops being a check and starts being the whole suite.
MAX_COVERING_PER_ROW = 3


def covering_tests_for(
    graph_db: str | None,
    repo_root: str,
    names: tuple[str, ...],
    *,
    limit: int = MAX_COVERING_PER_ROW,
) -> tuple[str, ...]:
    """Test files the graph says cover these definitions.

    Uses the producer's own targeted selection: direct covering edges, then a
    bounded caller closure, then the test-directory naming convention. This is
    the check that is derived from context rather than proposed by a model.
    """
    if not graph_db or not names:
        return ()
    try:
        from groundtruth.runtime.verification_plan import select_targeted_tests

        rows = select_targeted_tests(graph_db, repo_root, list(names), limit=limit)
    except Exception:  # noqa: BLE001 - selection is correct-or-quiet
        return ()
    files: list[str] = []
    for row in rows:
        path = str(row.get("file") or "").replace("\\", "/").lstrip("./")
        if path and path not in files:
            files.append(path)
    return tuple(files[:limit])


def default_check(
    test_command: tuple[str, ...], covering: tuple[str, ...]
) -> tuple[str, str]:
    """The command that would demonstrate a row, and what kind of check it is.

    Preference order is precision first: the tests the graph ties to this row's
    own definitions, then the repository's whole declared suite, then nothing.
    Returning nothing is a legitimate answer -- a row with no check is a row the
    plan must admit it cannot prove, not one it invents a command for.
    """
    if not test_command:
        return "", ""
    joined = " ".join(test_command)
    if covering:
        return "existing_test", f"{joined} {' '.join(covering)}"
    return "command", joined


def build_deterministic_plan(inputs: PlanInputs) -> PersistentPlan:
    """Every prompt requirement, anchored, with a check, before any model call."""
    rows: list[PlanRow] = []
    # The DISCOVERED command, not only a successfully parsed baseline run.
    #
    # Requiring `captured` tied every row's check to whether the pre-edit
    # baseline produced parseable output, which is a different question
    # entirely. Measured on run 34374028796: four of six tasks reported
    # no_tests_observed, so `captured` was false, so test_command was empty, so
    # default_check returned nothing and EVERY row in those plans shipped with
    # no way to prove it. awilix carried 27 requirements and 0 checks; boa
    # carried 41 and 0. The command itself was known all along -- discovery
    # populates it even when the run times out or prints nothing parseable.
    #
    # A command we discovered is a legitimate check. Whether the suite was green
    # beforehand is the regression baseline's business, and it is reported
    # separately.
    test_command = inputs.baseline.command
    for row in inputs.ledger.rows:
        anchors = inputs.anchors.anchors.get(row.row_id, ())
        covering = inputs.covering.get(row.row_id, ())
        kind, command = default_check(test_command, covering)
        rows.append(
            PlanRow(
                row_id=row.row_id,
                text=row.text,
                anchors=tuple(anchor.node_id for anchor in anchors),
                verification_kind=kind,
                verification_command=command,
            )
        )
    order = tuple(
        row_id
        for row_id in inputs.anchors.edit_order
        if any(row.row_id == row_id for row in rows)
    ) or tuple(row.row_id for row in rows)
    status = STATUS_PARTIAL if inputs.abstentions else STATUS_READY
    plan = PersistentPlan(
        status=status,
        inputs=inputs,
        rows=tuple(rows),
        interactions=(),
        edit_order=order,
        abstentions=tuple(inputs.abstentions),
        origin="deterministic",
    )
    plan.process_id = _process_id(plan)
    return plan


def _process_id(plan: PersistentPlan) -> str:
    import hashlib

    return hashlib.sha256(
        plan.canonical_json().encode("utf-8", "surrogatepass")
    ).hexdigest()
