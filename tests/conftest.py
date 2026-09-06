"""Shared test fixtures.

``write_certifiable_graph`` is the single authority for what a graph artifact
that ``certify_graph_artifact`` accepts actually looks like. Certification
binds a sealed index resource, the producer binary identity, real SQLite
bytes and the graph's own phase metadata to each other, so a stub manifest
with a handful of keys cannot certify - it fails at the first field it is
missing, which reads as a narrow mismatch rather than as "this is not a
producer certificate". Building it once keeps the shape honest in every
suite that needs one.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def tmp_workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def write_certifiable_graph(
    graph_state: Path,
    *,
    task_id: str,
    product_source_sha: str,
    repository_root_sha256: str = "b" * 64,
    source_manifest_sha256: str = "c" * 64,
    identity_scope: str = "benchmark_bound",
    cochange_rows: int = 2,
) -> dict[str, Any]:
    """Write graph.db, index-resource.json and graph.manifest.json as a set.

    Returns the manifest body. The three files are mutually bound by digest,
    so callers must not edit one without rewriting the others.
    """
    from gt_engine.indexer import _graph_phase_metadata, _sealed_json
    from gt_harness.product import groundtruth_release

    graph_state.mkdir(parents=True, exist_ok=True)
    graph_db = graph_state / "graph.db"
    with sqlite3.connect(graph_db) as db:
        db.execute("CREATE TABLE project_meta (key TEXT PRIMARY KEY, value TEXT)")
        db.execute("CREATE TABLE cochanges (id INTEGER)")
        db.executemany(
            "INSERT INTO cochanges VALUES (?)",
            [(index,) for index in range(1, cochange_rows + 1)],
        )
    binding = {
        "repository_root_sha256": repository_root_sha256,
        "source_manifest_sha256": source_manifest_sha256,
        "task_id": task_id,
        "product_source_sha": product_source_sha,
        "identity_scope": identity_scope,
    }
    producer = groundtruth_release()["producer_sha256"]
    resource_path = graph_state / "index-resource.json"
    _sealed_json(
        resource_path,
        {
            "schema": "gt.index_resource.v1", **binding,
            "status": "completed", "exit_code": 0, "error_code": "",
            "memory_evidence": False, "producer_binary_sha256": producer,
        },
        "evidence_sha256",
    )
    manifest = {
        "schema": "gt.graph_certification.v1",
        **binding,
        **_graph_phase_metadata(graph_db),
        "binary_certified": True,
        "binary_sha256": producer,
        "graph_sha256": hashlib.sha256(graph_db.read_bytes()).hexdigest(),
        "graph_bytes": graph_db.stat().st_size,
        "index_resource_sha256": hashlib.sha256(resource_path.read_bytes()).hexdigest(),
        "sqlite_quick_check": "ok",
        "cochange_rows": cochange_rows,
    }
    (graph_state / "graph.manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return manifest
