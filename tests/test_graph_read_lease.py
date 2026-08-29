from __future__ import annotations

import hashlib

import gt_engine.indexer as indexer
from gt_engine.indexer import IndexBuildReceipt, IndexBuildStatus


def test_certified_graph_read_binds_receipt_root_revision_and_bytes(tmp_path, monkeypatch):
    graph = tmp_path / "graph.db"
    graph.write_bytes(b"certified graph")
    digest = hashlib.sha256(graph.read_bytes()).hexdigest()
    receipt = IndexBuildReceipt(
        status=IndexBuildStatus.AVAILABLE,
        graph_db=str(graph),
        graph_revision="graph-r1",
        graph_db_sha256=digest,
        source_revision="source-r1",
        schema_valid=True,
    )
    monkeypatch.setattr(
        indexer,
        "_certify_published_graph",
        lambda *_args, **_kwargs: (True, ""),
    )
    monkeypatch.setattr(indexer, "_graph_logical_revision", lambda _path: "graph-r1")

    with indexer.certified_graph_read(
        receipt,
        expected_root=tmp_path,
        expected_source_revision="source-r1",
    ) as lease:
        assert lease.graph_path == graph.resolve()
        assert lease.graph_revision == "graph-r1"
        assert lease.source_revision == "source-r1"
        assert lease.graph_db_sha256 == digest
