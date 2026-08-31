from __future__ import annotations

from gt_engine.repository_intelligence import (
    CommunityEdge,
    build_leiden_communities,
)


def test_strict_and_inclusive_projections_are_deterministic_and_labeled() -> None:
    edges = (
        CommunityEdge("a", "b", "call", "verified", "edge-1"),
        CommunityEdge("b", "c", "call", "candidate", "edge-2"),
        CommunityEdge("c", "d", "import", "verified", "edge-3"),
        CommunityEdge("d", "external", "call", "external_unresolved", "edge-4"),
    )

    first = build_leiden_communities(
        nodes=("a", "b", "c", "d", "external", "isolate"),
        edges=edges,
        revision="graph-r1",
        seed=2512,
        resolution=1.0,
    )
    second = build_leiden_communities(
        nodes=("isolate", "external", "d", "c", "b", "a"),
        edges=tuple(reversed(edges)),
        revision="graph-r1",
        seed=2512,
        resolution=1.0,
    )

    assert first == second
    assert first.strict.projection_id == "strict_verified_v1"
    assert first.inclusive.projection_id == "inclusive_labeled_v1"
    assert first.strict.memberships == (
        ("a", "b"), ("c", "d"), ("external",), ("isolate",)
    )
    assert first.inclusive.memberships == (
        ("a", "b"), ("c", "d"), ("external",), ("isolate",)
    )
    assert first.inclusive.excluded_edges == ("edge-4",)
    assert first.strict.excluded_edges == ("edge-2", "edge-4")
    assert first.strict.input_digest == first.inclusive.input_digest


def test_modularity_splits_one_connected_graph_and_responds_to_resolution():
    nodes = ("a1", "a2", "a3", "b1", "b2", "b3")
    edges = tuple(
        CommunityEdge(left, right, "call", "verified", f"{left}-{right}")
        for left, right in (
            ("a1", "a2"), ("a1", "a3"), ("a2", "a3"),
            ("b1", "b2"), ("b1", "b3"), ("b2", "b3"),
            ("a3", "b1"),
        )
    )
    split = build_leiden_communities(
        nodes=nodes, edges=edges, revision="graph-r2", seed=2512, resolution=1.0
    )
    repeat = build_leiden_communities(
        nodes=tuple(reversed(nodes)),
        edges=tuple(reversed(edges)),
        revision="graph-r2", seed=2512, resolution=1.0,
    )
    coarse = build_leiden_communities(
        nodes=nodes, edges=edges, revision="graph-r2", seed=2512, resolution=0.1
    )

    assert split == repeat
    assert len(split.strict.memberships) == 2
    assert split.strict.memberships == (("a1", "a2", "a3"), ("b1", "b2", "b3"))
    assert split.strict.memberships != (nodes,)
    assert len(coarse.strict.memberships) <= len(split.strict.memberships)
    assert split.strict.algorithm_version == "gt.deterministic-modularity-leiden.v1"
