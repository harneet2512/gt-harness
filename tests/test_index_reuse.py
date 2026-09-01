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
