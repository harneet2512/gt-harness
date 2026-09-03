from __future__ import annotations

import sqlite3
from pathlib import Path

from gt_engine.indexer import _graph_node_count


def _graph(path: Path, *, nodes: int) -> Path:
    con = sqlite3.connect(path)
    try:
        con.execute("CREATE TABLE nodes (id INTEGER PRIMARY KEY)")
        con.executemany("INSERT INTO nodes (id) VALUES (?)", [(i,) for i in range(nodes)])
        con.commit()
    finally:
        con.close()
    return path


def test_a_populated_graph_is_counted(tmp_path: Path):
    assert _graph_node_count(_graph(tmp_path / "g.db", nodes=417)) == 417


def test_an_empty_graph_counts_zero(tmp_path: Path):
    """A task with no indexable source -- spreadsheets, data files -- is legitimate."""

    assert _graph_node_count(_graph(tmp_path / "g.db", nodes=0)) == 0


def test_a_missing_database_counts_zero_rather_than_raising(tmp_path: Path):
    assert _graph_node_count(tmp_path / "absent.db") == 0


def test_a_graph_without_a_nodes_table_counts_zero(tmp_path: Path):
    path = tmp_path / "g.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE other (id INTEGER)")
    con.commit()
    con.close()

    assert _graph_node_count(path) == 0


# --- the enforcement rule itself, exercised as pure logic -------------------
#
# Mirrors gt_harness.runtime_receipts: a populated graph that delivered no
# graph-backed evidence fails; an empty graph is exempt.


def _fails(*, indexed_nodes: int, graph_backed_delivery: bool) -> bool:
    return indexed_nodes > 0 and not graph_backed_delivery


def test_populated_graph_with_no_graph_evidence_fails():
    """Run 33708231670's shape: a treatment that ran without its mechanism."""

    assert _fails(indexed_nodes=12_000, graph_backed_delivery=False) is True


def test_populated_graph_that_delivered_graph_evidence_passes():
    assert _fails(indexed_nodes=12_000, graph_backed_delivery=True) is False


def test_empty_graph_with_no_graph_evidence_is_exempt():
    """Nothing to resolve is not the same as failing to resolve anything."""

    assert _fails(indexed_nodes=0, graph_backed_delivery=False) is False


def test_the_exemption_does_not_widen_as_the_graph_fills():
    """One node is enough to owe evidence -- the exemption is for empty, not small."""

    assert _fails(indexed_nodes=1, graph_backed_delivery=False) is True
