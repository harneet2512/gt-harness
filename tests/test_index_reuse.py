from __future__ import annotations

import hashlib
import json
import sqlite3

from gt_engine import indexer


def test_source_manifest_is_ordered_and_content_addressed(tmp_path) -> None:
    (tmp_path / "b.py").write_bytes(b"b\n")
    (tmp_path / "a.py").write_bytes(b"a\n")
    first = indexer.source_manifest_digest(tmp_path)
    (tmp_path / "ignored.txt").write_bytes(b"irrelevant")
    assert indexer.source_manifest_digest(tmp_path) == first
    (tmp_path / "a.py").write_bytes(b"changed\n")
    assert indexer.source_manifest_digest(tmp_path) != first


def test_index_reuse_key_binds_manifest_binary_and_schema(tmp_path, monkeypatch) -> None:
    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.setattr(
        indexer,
        "_binary_certification",
        lambda: {"binary_sha256": "bin-1", "path_sha256": "path"},
    )
    key = indexer.compute_index_reuse_key(tmp_path)
    assert key.as_dict()["source_manifest_sha256"] == indexer.source_manifest_digest(tmp_path)
    assert key.as_dict()["producer_binary_sha256"] == "bin-1"
    expected = hashlib.sha256(
        json.dumps(key.as_dict(), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert key.digest == expected


def test_quick_check_rejects_corrupt_reuse_candidate(tmp_path) -> None:
    graph = tmp_path / "graph.db"
    with sqlite3.connect(graph) as con:
        con.execute("create table project_meta (key text)")
    manifest = tmp_path / "graph.manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    ok, reason = indexer._graph_schema_receipt(graph)
    assert ok is True
    assert reason == "ok"
    graph.write_bytes(b"not sqlite")
    ok, reason = indexer._graph_schema_receipt(graph)
    assert ok is False
    assert reason


def test_graph_phase_metadata_distinguishes_core_only_and_verifies_seals(tmp_path) -> None:
    graph = tmp_path / "graph.db"
    analysis = json.dumps({"schema": "gt-index.analysis-phase.v1", "state": "failed", "failure_reason": "budget"}, separators=(",", ":"))
    core = json.dumps({"schema": "gt-index.core-phase.v1", "state": "committed"}, separators=(",", ":"))
    with sqlite3.connect(graph) as con:
        con.execute("create table project_meta (key text primary key, value text)")
        con.execute("create table cochanges (id integer)")
        con.executemany(
            "insert into project_meta(key,value) values(?,?)",
            [
                ("core_phase_state", "committed"),
                ("core_phase_receipt", core),
                ("core_phase_receipt_sha256", hashlib.sha256(core.encode()).hexdigest()),
                ("analysis_state", "failed"),
                ("analysis_failure_reason", "budget"),
                ("analysis_phase_receipt", analysis),
                ("analysis_phase_receipt_sha256", hashlib.sha256(analysis.encode()).hexdigest()),
            ],
        )
        con.executemany("insert into cochanges(id) values(?)", [(1,), (2,)])

    ok, reason = indexer._graph_schema_receipt(graph)
    assert (ok, reason) == (True, "ok")
    meta = indexer._graph_phase_metadata(graph)
    assert meta["core_phase_state"] == "committed"
    assert meta["analysis_state"] == "failed"
    assert meta["analysis_failure_reason"] == "budget"
    assert meta["cochange_rows"] == 2
    # Derived-layer keys are "unrecorded" / 0 when the tables don't exist
    assert meta["derived_layers_state"] == "unrecorded"
    assert meta["community_rows"] == 0
    assert meta["process_rows"] == 0

    with sqlite3.connect(graph) as con:
        con.execute("update project_meta set value='0' where key='analysis_phase_receipt_sha256'")
    ok, reason = indexer._graph_schema_receipt(graph)
    assert ok is False
    assert reason == "analysis_phase_receipt_sha256_mismatch"
