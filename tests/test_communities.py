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
        nodes=("a", "b", "c", "d", "isolate"),
        edges=edges,
        revision="graph-r1",
        seed=2512,
        resolution=1.0,
    )
    second = build_leiden_communities(
        nodes=("isolate", "d", "c", "b", "a"),
        edges=tuple(reversed(edges)),
        revision="graph-r1",
        seed=2512,
        resolution=1.0,
    )

    assert first == second
    assert first.strict.projection_id == "strict_verified_v1"
    assert first.inclusive.projection_id == "inclusive_labeled_v1"
    assert first.strict.memberships == (("a", "b"), ("c", "d"), ("isolate",))
    assert first.inclusive.memberships == (("a", "b", "c", "d"), ("external",), ("isolate",))
    assert first.inclusive.excluded_edges == ("edge-4",)
    assert first.strict.excluded_edges == ("edge-2", "edge-4")
    assert first.strict.input_digest == first.inclusive.input_digest

