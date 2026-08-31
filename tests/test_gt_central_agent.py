from __future__ import annotations

import json

import pytest

from gt_engine.persistent_execution_state import (
    ExecutionStateSnapshot,
    ProcessStep,
    build_planning_payload,
    build_witnessed_process,
)


def _process():
    return build_witnessed_process(
        anchors=("entry", "worker"),
        steps=(ProcessStep("entry", "edge-1", "evidence-1", "calls", "verified"),),
        branches=(("entry", "worker"),),
        gaps=("worker body unavailable",),
        graph_revision="graph-r1",
        source_revision="source-r1",
        projection="strict_verified_v1",
    )


def test_planning_consumes_reopened_process_without_inventing_steps(tmp_path):
    process = _process()
    state = ExecutionStateSnapshot("source-r1", "workspace-r1", "graph-r1", "graph.db")
    path = tmp_path / "process.json"
    state.persist_witnessed_process(path, process)
    before = path.read_bytes()
    reopened_state, reopened = ExecutionStateSnapshot.reopen_witnessed_process(path)
    assert reopened_state == state
    assert reopened.receipt == process.receipt
    assert path.read_bytes() == before

    payload = build_planning_payload(
        reopened, source_revision="source-r1", graph_revision="graph-r1"
    )
    assert payload["status"] == "PARTIAL"
    assert payload["steps"] == ("entry",)
    assert payload["gaps"] == ("worker body unavailable",)
    assert payload["citations"][0]["evidence_id"] == "evidence-1"
    assert payload["citations"][0]["source_revision"] == "source-r1"
    assert "worker" not in payload["steps"]


def test_reopened_process_rejects_receipt_and_revision_mutation(tmp_path):
    process = _process()
    state = ExecutionStateSnapshot("source-r1", "workspace-r1", "graph-r1", "graph.db")
    path = tmp_path / "process.json"
    state.persist_witnessed_process(path, process)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["process"]["steps"][0]["node_id"] = "invented"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="receipt mismatch"):
        ExecutionStateSnapshot.reopen_witnessed_process(path)

    with pytest.raises(ValueError, match="source revision"):
        state.persist_witnessed_process(tmp_path / "bad.json", build_witnessed_process(
            anchors=("entry",), steps=(), branches=(), gaps=(),
            graph_revision="graph-r1", source_revision="source-r2",
            projection="strict_verified_v1",
        ))
