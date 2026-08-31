from __future__ import annotations

from gt_engine.persistent_execution_state import (
    ProcessStep,
    build_witnessed_process,
)


def test_witnessed_process_is_stable_and_revision_bound() -> None:
    steps = (
        ProcessStep("node-a", "edge-a", "evidence-a", "call", "verified"),
        ProcessStep("node-b", "edge-b", "evidence-b", "return", "candidate"),
    )
    first = build_witnessed_process(
        anchors=("node-a", "node-b"),
        steps=steps,
        branches=(("node-a", "node-b"),),
        gaps=("missing-node",),
        graph_revision="graph-r1",
        source_revision="source-r1",
        projection="inclusive_labeled_v1",
    )
    second = build_witnessed_process(
        anchors=("node-b", "node-a"),
        steps=tuple(reversed(steps)),
        branches=(("node-a", "node-b"),),
        gaps=("missing-node",),
        graph_revision="graph-r1",
        source_revision="source-r1",
        projection="inclusive_labeled_v1",
    )

    assert first == second
    assert first.process_id
    assert first.verification_state == "witnessed"
    assert first.has_gaps is True
    assert first.steps[1].verification_state == "candidate"
    assert first.receipt["graph_revision"] == "graph-r1"


def test_stale_graph_abstains_and_source_revision_changes_identity() -> None:
    step = ProcessStep("node-a", "edge-a", "evidence-a", "call", "verified")
    stale = build_witnessed_process(
        anchors=("node-a",),
        steps=(step,),
        branches=(),
        gaps=(),
        graph_revision="graph-old",
        source_revision="source-r1",
        projection="strict_verified_v1",
        current_graph_revision="graph-new",
    )
    changed = build_witnessed_process(
        anchors=("node-a",),
        steps=(step,),
        branches=(),
        gaps=(),
        graph_revision="graph-old",
        source_revision="source-r2",
        projection="strict_verified_v1",
    )

    assert stale.verification_state == "abstained"
    assert stale.stale_reason == "graph_revision_stale"
    assert stale.process_id != changed.process_id
