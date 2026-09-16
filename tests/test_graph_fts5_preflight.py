from __future__ import annotations

import sqlite3

from gt_engine import indexer

# The gate-one paid run (35056493769) adopted a graph whose external-content
# nodes_fts carried a dead index generation: nodes_fts_docsize held 190,953
# rows over 95,644 nodes, bm25's idf term went log(<=0) -> NaN -> NULL under
# the consumer's SQLite, and lexical_rank crashed on float(None). These tests
# pin the preflight half of the fix: _graph_schema_receipt — the single choke
# point every build/amend/merge/publication path passes — must refuse a graph
# whose declared index cannot answer a real scored query, and must refuse an
# absent index when the run is benchmark-bound.


def _graph(tmp_path, *, with_fts: bool = True, nodes: int = 4):
    graph = tmp_path / "graph.db"
    with sqlite3.connect(graph) as con:
        con.execute("CREATE TABLE project_meta (key TEXT PRIMARY KEY, value TEXT)")
        con.execute("CREATE TABLE nodes (id INTEGER PRIMARY KEY, name TEXT)")
        # The shared "clone" token spans two rows, so a single-term MATCH on it
        # exceeds any forged stats claim of 1 indexed row.
        names = [
            "clone_alpha", "clone_beta", "gamma", "delta", "epsilon", "zeta",
        ][:nodes]
        con.executemany("INSERT INTO nodes(id, name) VALUES (?, ?)", enumerate(names, 1))
        if with_fts:
            con.execute(
                "CREATE VIRTUAL TABLE nodes_fts USING fts5("
                "name, content='nodes', content_rowid='id')"
            )
            con.execute("INSERT INTO nodes_fts(nodes_fts) VALUES('rebuild')")
    return graph


def _corrupt_averages(graph, claimed_rows: int) -> None:
    """Force the production inconsistency: stats claim fewer rows than the
    index holds, so bm25's idf goes non-finite -> NULL on this driver."""
    with sqlite3.connect(graph) as con:
        (block,) = con.execute("SELECT block FROM nodes_fts_data WHERE id=1").fetchone()
        con.execute(
            "UPDATE nodes_fts_data SET block=? WHERE id=1",
            (bytes([claimed_rows]) + block[1:],),
        )


def test_preflight_accepts_a_healthy_index(tmp_path) -> None:
    graph = _graph(tmp_path)
    ok, reason = indexer._graph_schema_receipt(graph)
    assert (ok, reason) == (True, "ok")


def test_preflight_refuses_a_desynced_index(tmp_path) -> None:
    graph = _graph(tmp_path)
    with sqlite3.connect(graph) as con:
        con.execute("INSERT INTO nodes_fts_docsize(id, sz) VALUES (9999, 1)")
    ok, reason = indexer._graph_schema_receipt(graph)
    assert ok is False
    assert reason == "fts5_invalid:nodes_fts_desynced:5of4"


def test_preflight_refuses_null_bm25(tmp_path) -> None:
    graph = _graph(tmp_path, nodes=6)
    _corrupt_averages(graph, 1)
    # Premise: "clone" is one term spanning two rows — its doclist is longer
    # than the forged nRow, so bm25 returns NULL on this driver.
    with sqlite3.connect(graph) as con:
        scores = [r[0] for r in con.execute(
            "SELECT bm25(nodes_fts) FROM nodes_fts WHERE nodes_fts MATCH 'clone'"
        )]
    assert any(s is None for s in scores), "fixture did not reproduce NULL bm25"
    ok, reason = indexer._graph_schema_receipt(graph)
    assert ok is False
    assert reason == "fts5_invalid:nodes_fts_bm25_invalid"


def test_preflight_refuses_a_malformed_index(tmp_path) -> None:
    graph = _graph(tmp_path)
    with sqlite3.connect(graph) as con:
        con.execute("DELETE FROM nodes_fts_docsize")
    ok, reason = indexer._graph_schema_receipt(graph)
    assert ok is False
    assert reason  # DatabaseError: database disk image is malformed — typed refusal


def test_preflight_tolerates_absent_index_locally(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("GT_TASK_ID", raising=False)
    monkeypatch.delenv("GT_PRODUCT_SOURCE_SHA", raising=False)
    graph = _graph(tmp_path, with_fts=False)
    ok, reason = indexer._graph_schema_receipt(graph)
    assert (ok, reason) == (True, "ok")


def test_preflight_refuses_absent_index_when_benchmark_bound(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GT_TASK_ID", "task-x")
    monkeypatch.setenv("GT_PRODUCT_SOURCE_SHA", "a" * 40)
    graph = _graph(tmp_path, with_fts=False)
    ok, reason = indexer._graph_schema_receipt(graph)
    assert ok is False
    assert reason == "fts5_invalid:nodes_fts_absent"


def test_index_child_env_arms_require_fts5_only_when_benchmark_bound(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("GT_TASK_ID", raising=False)
    monkeypatch.delenv("GT_PRODUCT_SOURCE_SHA", raising=False)
    assert "GT_REQUIRE_FTS5" not in indexer._index_child_environment(1 << 30)
    monkeypatch.setenv("GT_TASK_ID", "task-x")
    monkeypatch.setenv("GT_PRODUCT_SOURCE_SHA", "b" * 40)
    assert indexer._index_child_environment(1 << 30)["GT_REQUIRE_FTS5"] == "1"
