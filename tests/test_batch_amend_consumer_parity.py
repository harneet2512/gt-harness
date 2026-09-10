"""Every graph consumer must answer the same question of both graphs.

Table equality is necessary and not sufficient. A consumer reads the graph
through joins, FTS indexes, closure depths and confidence thresholds, and two
databases can agree row for row on the tables this project compares while a
consumer still answers differently -- an FTS index that was not republished, a
closure whose depths were retained from the parent, a symbol id form that
changed under a caller. Those are exactly the failures that made the amend
worth distrusting: on a real run caller coverage was answered on 18 of 33
post-edit rebuilds and 31 of 33 were refused outright.

So this asks the question the consumers actually ask, of an amended graph and
of a graph rebuilt from the same tree, and requires the same answer. Answers
are compared by NAME and PATH rather than by node id: the amend retains parent
ids deliberately and a rebuild has no reason to choose the same numbers, so
comparing ids would fail on a difference no consumer can observe.
"""
from __future__ import annotations

import sys

import pytest

from gt_engine import indexer
from gt_engine.task_contract import extract_task_contract

from .test_batch_amend_parity import BASE, CASES, _build, _write

TASK = (
    "Change the child so it keeps working.\n"
    "\n"
    "Assumptions:\n"
    " Child.run must keep returning a number\n"
    " helper must stay importable from pkg.other\n"
)


def _projection(graph: str) -> dict:
    from gt_engine.graph_context import build_graph_projection

    result = build_graph_projection(graph, extract_task_contract(TASK))
    return {
        "files": sorted(result.files),
        "symbols": sorted(result.symbols),
        # node_ids are deliberately excluded: the amend retains parent ids and
        # a rebuild renumbers, and no consumer can observe the difference.
    }


def _surfaces(graph: str) -> dict:
    from gt_engine.graph_context import graph_surface_receipt

    receipt = graph_surface_receipt(graph)
    return {"available": receipt["available"], "surfaces": dict(receipt["surfaces"])}


def _anchors(graph: str) -> dict:
    from gt_engine.persistent_plan.anchors import build_anchor_result
    from gt_engine.persistent_plan.ledger import build_requirement_ledger

    ledger = build_requirement_ledger(TASK)
    result = build_anchor_result(graph, ledger)
    return {
        "anchors": {
            row_id: sorted((a.name, a.qualified_name, a.file_path, a.label, a.basis)
                           for a in anchors)
            for row_id, anchors in result.anchors.items()
        },
        "modes": sorted((m.symbol, m.defining_file, tuple(sorted(m.members)))
                        for m in result.modes),
        "abstentions": sorted(result.abstentions),
    }


def _callers(graph: str) -> dict:
    from gt_engine.persistent_plan.anchors import build_anchor_result
    from gt_engine.persistent_plan.ledger import build_requirement_ledger

    result = build_anchor_result(graph, build_requirement_ledger(TASK))
    lookup = {a.node_id: a for anchors in result.anchors.values() for a in anchors}
    return {
        (lookup[node_id].qualified_name if node_id in lookup else str(node_id)): sorted(
            (c.name, c.file_path) for c in callers
        )
        for node_id, callers in result.callers.items()
    }


def _ego(graph: str) -> dict:
    """Callers and callees, named.

    ``EgoGraph.nodes`` is a mapping keyed by node id, and `callers`/`callees`
    resolve those ids to node objects. Comparing the ids themselves would fail
    on a renumbering no consumer can observe; comparing the names and files
    they resolve to is the question the consumer actually asks.
    """
    from groundtruth.graph.ego import change_impact, ego_graph

    out: dict = {}
    for symbol, path in (("run", "pkg/child.py"), ("save", "pkg/base.py"),
                         ("main", "app.py"), ("helper", "pkg/other.py")):
        try:
            ego = ego_graph(graph, symbol, path, k=1)
            neighbours = {
                relation: sorted(
                    (str(getattr(item, "name", "")), str(getattr(item, "file_path", "")))
                    for item in getattr(ego, relation, ()) or ()
                )
                for relation in ("callers", "callees")
            }
            neighbours["signature"] = str(getattr(ego, "signature", ""))
            neighbours["guards"] = sorted(str(g) for g in getattr(ego, "guards", ()) or ())
            neighbours["obligations"] = sorted(
                str(o) for o in getattr(ego, "obligations", ()) or ())
            neighbours["test_assertions"] = sorted(
                str(a) for a in getattr(ego, "test_assertions", ()) or ())
        except Exception as exc:  # noqa: BLE001 - an absent symbol is an answer
            neighbours = {"<error>": type(exc).__name__}
        try:
            impact = sorted(
                (str(row.get("name", "")), str(row.get("file_path", "")))
                for row in change_impact(graph, symbol, path, max_depth=2)
            )
        except Exception as exc:  # noqa: BLE001
            impact = [("<error>", type(exc).__name__)]
        out[f"{path}::{symbol}"] = {"ego": neighbours, "impact": impact}
    return out


def _covering(graph: str, repo_root: str) -> dict:
    from gt_engine.persistent_plan.deterministic import covering_tests_for

    return {
        name: list(covering_tests_for(graph, repo_root, (name,)))
        for name in ("run", "save", "main", "helper")
    }


def _contracts(graph: str) -> dict:
    import sqlite3
    from contextlib import closing

    from gt_engine.contract import symbol_contract

    with closing(sqlite3.connect(f"file:{graph}?mode=ro", uri=True)) as db:
        rows = db.execute(
            "SELECT id,qualified_name FROM nodes WHERE label IN ('Function','Method','Class')"
            " ORDER BY qualified_name, file_path, start_line"
        ).fetchall()
    out: dict = {}
    for node_id, qualified in rows:
        try:
            contract = symbol_contract(graph, int(node_id))
        except Exception as exc:  # noqa: BLE001 - a refusal is an answer
            out[qualified] = {"<error>": type(exc).__name__}
            continue
        out[qualified] = _without_row_ids(contract)
    return out


def _without_row_ids(value):
    """Drop every row-id-shaped key, at any depth.

    The contract carries `property_ids` and per-shape `property_id` values
    alongside the facts. Those are storage addresses: the amend retains parent
    ids by design and a rebuild renumbers, and no consumer can observe the
    difference. What a consumer reads is what the contract SAYS, so the
    comparison keeps the values and drops the addresses. Anything else makes
    this test fail on the amend's whole reason for existing.
    """
    if isinstance(value, dict):
        return {
            key: _without_row_ids(item) for key, item in sorted(value.items())
            if not (str(key).endswith(("_id", "_ids")) or str(key) in {"id", "ids"})
        }
    if isinstance(value, (list, tuple)):
        return [_without_row_ids(item) for item in value]
    return value


def _cochanges(graph: str) -> int:
    from gt_engine.cochange_evidence import cochange_row_count

    return cochange_row_count(graph)


CONSUMERS = {
    "graph_projection": lambda graph, root: _projection(graph),
    "surface_receipt": lambda graph, root: _surfaces(graph),
    "plan_anchors": lambda graph, root: _anchors(graph),
    "plan_callers": lambda graph, root: _callers(graph),
    "ego_and_impact": lambda graph, root: _ego(graph),
    "covering_tests": lambda graph, root: _covering(graph, root),
    "symbol_contracts": lambda graph, root: _contracts(graph),
    "cochange_count": lambda graph, root: _cochanges(graph),
}


@pytest.mark.skipif(not sys.platform.startswith("linux"),
                    reason="installed Linux producer required")
@pytest.mark.parametrize(("case", "mutation", "_changed"), CASES)
def test_every_consumer_answers_the_same_of_an_amended_and_a_rebuilt_graph(
    tmp_path, case, mutation, _changed
):
    binary = indexer._resolved_binary_path()
    assert binary, "provide the exact candidate Linux binary"

    root = tmp_path / "repo"
    root.mkdir()
    _write(root, BASE)
    state = tmp_path / "state"
    parent = _build(root, state, "parent")

    _write(root, mutation)
    amended = _build(root, state, f"amended_{case}", parent)
    rebuilt = _build(root, state, f"rebuilt_{case}")

    differing = []
    for name, ask in CONSUMERS.items():
        if ask(str(amended), str(root)) != ask(str(rebuilt), str(root)):
            differing.append(name)
    assert not differing, f"{case}: consumers disagreed: {differing}"


@pytest.mark.skipif(not sys.platform.startswith("linux"),
                    reason="installed Linux producer required")
def test_the_consumer_panel_can_tell_two_different_graphs_apart(tmp_path):
    """A panel that answers identically for every graph proves nothing.

    Each consumer is asked of the pre-mutation parent and of the rebuild, and
    at least one must disagree for every case -- otherwise the equality above
    is satisfied by questions none of these consumers can answer.
    """
    binary = indexer._resolved_binary_path()
    assert binary, "provide the exact candidate Linux binary"

    blind = []
    answered: set[str] = set()
    for parameters in CASES:
        case, mutation, _changed = parameters.values
        root = tmp_path / case
        root.mkdir()
        _write(root, BASE)
        state = tmp_path / f"state_{case}"
        parent = _build(root, state, "parent")
        _write(root, mutation)
        rebuilt = _build(root, state, "rebuilt")
        moved = {name for name, ask in CONSUMERS.items()
                 if ask(str(parent), str(root)) != ask(str(rebuilt), str(root))}
        answered |= moved
        if not moved:
            blind.append(case)
    assert not blind, f"no consumer noticed these mutations at all: {blind}"
    assert len(answered) >= 4, f"only these consumers ever moved: {sorted(answered)}"
