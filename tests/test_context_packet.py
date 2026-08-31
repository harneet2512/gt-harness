from __future__ import annotations

import copy

import pytest

from gt_engine.context_packet import (
    ContextPacketAbstention,
    build_context_packet,
    build_fixture_matrix,
    verify_context_packet,
)


def test_fixture_matrix_covers_three_files_and_boundaries() -> None:
    fixtures = build_fixture_matrix(source_revision="src-1", graph_revision="graph-1")
    assert len(fixtures) == 9
    assert all(verify_context_packet(item.packet) for item in fixtures)
    assert len({item.packet["packet_digest_sha256"] for item in fixtures}) == 9


def test_packet_is_byte_stable_and_confidence_ranked() -> None:
    kwargs = dict(
        source_revision="src-1",
        graph_revision="graph-1",
        file_path="src/a.py",
        boundary="view",
        claims=(
            {"claim_id": "z", "kind": "x", "confidence": 0.2},
            {"claim_id": "a", "kind": "x", "confidence": 0.9},
        ),
    )
    assert build_context_packet(**kwargs) == build_context_packet(**kwargs)
    assert [row["claim_id"] for row in build_context_packet(**kwargs)["claims"]] == ["a", "z"]


def test_tamper_and_stale_revision_are_rejected() -> None:
    packet = build_context_packet(
        source_revision="src-1",
        graph_revision="graph-1",
        file_path="src/a.py",
        boundary="open",
        claims=({"claim_id": "a", "kind": "definition", "confidence": 0.9},),
    )
    tampered = copy.deepcopy(packet)
    tampered["claims"][0]["confidence"] = 0.1
    assert not verify_context_packet(tampered)
    with pytest.raises(ContextPacketAbstention, match="stale_graph_revision"):
        build_context_packet(
            source_revision="src-1",
            graph_revision="graph-1",
            file_path="src/a.py",
            boundary="open",
            claims=({"claim_id": "a", "kind": "definition", "confidence": 0.9, "graph_revision": "graph-old"},),
        )

