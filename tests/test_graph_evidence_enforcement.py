from __future__ import annotations

import sqlite3
from pathlib import Path

from gt_engine.indexer import _graph_scale


def _graph(path: Path, *, files: int, nodes: int) -> Path:
    con = sqlite3.connect(path)
    try:
        con.execute("CREATE TABLE file_hashes (path TEXT PRIMARY KEY)")
        con.execute("CREATE TABLE nodes (id INTEGER PRIMARY KEY)")
        con.executemany(
            "INSERT INTO file_hashes (path) VALUES (?)", [(f"f{i}.go",) for i in range(files)]
        )
        con.executemany("INSERT INTO nodes (id) VALUES (?)", [(i,) for i in range(nodes)])
        con.commit()
    finally:
        con.close()
    return path


def test_scale_reports_files_and_nodes_separately(tmp_path: Path):
    assert _graph_scale(_graph(tmp_path / "g.db", files=120, nodes=4_300)) == (120, 4_300)


def test_a_repository_with_no_source_reports_no_files(tmp_path: Path):
    assert _graph_scale(_graph(tmp_path / "g.db", files=0, nodes=0)) == (0, 0)


def test_counts_fail_closed_on_an_unreadable_graph(tmp_path: Path):
    assert _graph_scale(tmp_path / "absent.db") == (0, 0)


def test_counts_fail_closed_on_a_schemaless_graph(tmp_path: Path):
    path = tmp_path / "g.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE other (id INTEGER)")
    con.commit()
    con.close()

    assert _graph_scale(path) == (0, 0)


# --- the enforcement rule, exercised as pure logic --------------------------
#
# Mirrors gt_harness.runtime_receipts. The rule keys on indexed FILES, not
# nodes: a repository that had source to index owes graph-derived evidence.


def _fails(*, indexed_files: int, graph_backed_delivery: bool) -> bool:
    return indexed_files > 0 and not graph_backed_delivery


def test_a_complete_repository_owes_graph_evidence():
    """Files present, graph built, nothing delivered — run 33708231670's shape."""

    assert _fails(indexed_files=120, graph_backed_delivery=False) is True


def test_files_indexed_but_no_nodes_produced_still_fails():
    """A broken index is not an empty one.

    Keying the exemption on node count would have excused exactly this: a
    repository full of source whose index produced nothing would look
    indistinguishable from a task with nothing to index.
    """

    # 120 files walked, zero nodes emitted: the graph is broken, not empty.
    assert _fails(indexed_files=120, graph_backed_delivery=False) is True


def test_a_task_starting_with_no_source_is_a_wait_state_not_a_failure():
    """Nothing to index yet — the graph fills as the agent creates files."""

    assert _fails(indexed_files=0, graph_backed_delivery=False) is False


def test_delivering_graph_evidence_discharges_the_obligation():
    assert _fails(indexed_files=120, graph_backed_delivery=True) is False


def test_one_indexed_file_is_enough_to_owe_evidence():
    """The exemption is for having no source, not for having little."""

    assert _fails(indexed_files=1, graph_backed_delivery=False) is True
