from __future__ import annotations

import copy
import hashlib
import json

import pytest

from gt_engine.context_packet import (
    ContextPacketAbstention,
    ContextPacketError,
    build_context_packet,
    build_fixture_matrix,
    serve_context_packet,
    verify_context_packet,
)
from gt_engine.evidence_router import EvidenceRouter, verify_eligibility_receipt
from gt_engine.task_contract import TaskContract


def claim(claim_id="a", statement="The declaration is at src/a.py:1", **kwargs):
    return {
        "claim_id": claim_id, "kind": "definition", "confidence": 0.9,
        "payload": {"statement": statement},
        "evidence_refs": ["observation-1"],
        "sources": [{"path": "src/a.py", "start_line": 1, "end_line": 1,
                     "content_sha256": hashlib.sha256(b"def a(): pass\n").hexdigest()}],
        **kwargs,
    }


def packet_for(*claims, **kwargs):
    return build_context_packet(source_revision="src-1", graph_revision="graph-1",
                                file_path="src/a.py", boundary="view",
                                claims=claims or (claim(),), **kwargs)


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
            claim("z", confidence=0.2),
            claim("a", confidence=0.9),
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
        claims=(claim(),),
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
            claims=(claim(graph_revision="graph-old"),),
        )


def test_serving_seals_packet_through_eligibility_router() -> None:
    router = EvidenceRouter(TaskContract(role="content_scan", obligations=()))
    transported, receipt = serve_context_packet(
        source_revision="src-1",
        graph_revision="graph-1",
        file_path="src/a.py",
        boundary="view",
        claims=(claim("definition"),),
        eligibility_router=router,
        decision_id="decision-1",
        iteration_id="iteration-1",
    )
    packet = json.loads(transported["messages"][0]["content"])
    assert verify_context_packet(packet)
    assert verify_eligibility_receipt(receipt)
    assert receipt["claims"][0]["claim_id"] == packet["packet_id"]


def test_substantive_fact_is_preserved_and_bound_into_identity():
    first = packet_for(claim(statement="Caller requires a new argument"))
    second = packet_for(claim(statement="Caller requires no change"))
    assert first["claims"][0]["payload"]["statement"] == "Caller requires a new argument"
    assert first["claims"][0]["sources"] == claim()["sources"]
    assert first["packet_id"] != second["packet_id"]
    assert first["packet_digest_sha256"] != second["packet_digest_sha256"]
    assert first["schema"] == "gt.context_packet.v2"


@pytest.mark.parametrize("field", ["payload", "evidence_refs", "sources"])
def test_missing_fact_or_provenance_is_rejected(field):
    value = claim()
    del value[field]
    with pytest.raises(ContextPacketError):
        packet_for(value)


def test_budget_includes_digest_field():
    packet = packet_for()
    unsigned_size = len(json.dumps({k: v for k, v in packet.items()
                                   if k != "packet_digest_sha256"},
                                  sort_keys=True, separators=(",", ":")).encode())
    with pytest.raises(ContextPacketAbstention, match="byte_budget_exceeded"):
        packet_for(byte_budget=unsigned_size + 10)


def test_duplicate_identity_and_dangling_edges_rejected():
    with pytest.raises(ContextPacketError):
        packet_for(claim(), claim(statement="opposite assertion"))
    with pytest.raises(ContextPacketError):
        packet_for(edges=[{"edge_id": "e", "kind": "calls", "from_claim": "a",
                           "to_claim": "missing"}])


def test_router_refusal_never_returns_unadmitted_packet():
    class RefusingRouter:
        def admit_decision(self, **kwargs):
            return kwargs["baseline_request"], {"status": "DEGRADED"}

    baseline = {"messages": [{"role": "user", "content": "native"}]}
    transported, receipt = serve_context_packet(
        source_revision="src-1", graph_revision="graph-1", file_path="src/a.py",
        boundary="view", claims=(claim(),), eligibility_router=RefusingRouter(),
        decision_id="d", iteration_id="i", baseline_request=baseline,
    )
    assert transported is baseline
    assert receipt["status"] == "DEGRADED"


@pytest.mark.parametrize("value", [None, [], {}, {"schema": "gt.context_packet.v2"}])
def test_malformed_packet_verification_is_false_not_exception(value):
    assert verify_context_packet(value) is False

