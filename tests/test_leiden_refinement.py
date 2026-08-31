from __future__ import annotations

import pytest

from gt_engine.repository_intelligence import (
    CommunityEdge,
    build_leiden_communities,
    verify_community_run,
)


def _run():
    nodes = ["a", "b", "c", "d"]
    edges = [
        CommunityEdge("a", "b", "CALL", "verified", "e1"),
        CommunityEdge("b", "c", "CALL", "verified", "e2"),
        CommunityEdge("c", "d", "CALL", "candidate", "e3"),
    ]
    return build_leiden_communities(nodes=nodes, edges=edges, revision="r1", seed=7)


def test_refinement_is_deterministic_and_emits_certificates():
    run = _run()
    assert run.strict.algorithm_version_v2 == "gt.deterministic-leiden-refinement.v2"
    receipt = run.strict.receipt
    assert receipt["algorithm_version"] == "gt.deterministic-leiden-refinement.v2"
    assert receipt["modularity"] is not None
    assert receipt["admitted_edge_count"] == 2
    assert receipt["refinement_digest"]
    assert receipt["connectivity_witnesses"]
    assert verify_community_run(run)
    assert run == _run()


def test_membership_connectivity_and_digest_mutations_are_rejected():
    run = _run()
    with pytest.raises(ValueError, match="membership"):
        verify_community_run(run, expected_membership_digest="bad")
    assert verify_community_run(run)

