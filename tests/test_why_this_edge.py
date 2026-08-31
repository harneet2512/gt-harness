from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from gt_engine.miniswe_typed_actions import execute_typed_action_fail_open
from gt_engine.why_this_edge import (
    ALLOWED_EDGE_KINDS,
    WhyThisEdgeAbstention,
    WhyThisEdgeStore,
    certify_why_this_edge,
    harvest_resolution_substrate,
    query_why_this_edge,
    verify_why_this_edge,
    why_this_edge_receipt,
)


def _facts() -> dict:
    return {
        "edge_id": "edge-1",
        "callsite_id": "call-1",
        "edge_kind": "SELECTED_TARGET",
        "target_id": "target-a",
        "dispatch_state": "unique",
        "candidate_count": 2,
        "candidates": [
            {"target_id": "target-a", "flow_witnesses": ["flow-a"]},
            {"target_id": "target-b", "flow_witnesses": ["flow-b"]},
        ],
        "flow_witnesses": {
            "target-a": ["flow-a"],
            "target-b": ["flow-b"],
        },
        "source_revision": "src-1",
        "graph_revision": "graph-1",
        "completion_identity": "build-1",
        "complete": True,
    }


def test_certified_edge_preserves_candidates_and_witness_conservation():
    result = certify_why_this_edge(_facts())
    assert result["schema"] == "gt.why_this_edge.v1"
    assert result["candidate_count"] == 2
    assert result["dispatch_state"] == "unique"
    assert result["digest_sha256"]
    assert verify_why_this_edge(result)


def test_query_is_deterministic_and_rejects_missing_swapped_or_invented_witnesses():
    first = query_why_this_edge(_facts())
    second = query_why_this_edge(json.loads(json.dumps(_facts())))
    assert first == second
    swapped = _facts()
    swapped["flow_witnesses"] = {"target-a": ["flow-b"], "target-b": ["flow-a"]}
    with pytest.raises(WhyThisEdgeAbstention, match="witness_conservation"):
        query_why_this_edge(swapped)
    invented = _facts()
    invented["flow_witnesses"]["target-x"] = ["flow-x"]
    with pytest.raises(WhyThisEdgeAbstention, match="witness_conservation"):
        query_why_this_edge(invented)


def test_unsupported_unknown_stale_and_incomplete_facts_abstain_typed():
    assert ALLOWED_EDGE_KINDS == {"HAS_CALLSITE", "CANDIDATE_TARGET", "SELECTED_TARGET"}
    for mutation in (
        {"edge_kind": "OTHER"},
        {"edge_kind": "SELECTED_TARGET", "graph_revision": "stale"},
        {"edge_kind": "SELECTED_TARGET", "complete": False},
    ):
        facts = _facts()
        facts.update(mutation)
        with pytest.raises(WhyThisEdgeAbstention):
            query_why_this_edge(facts, expected_graph_revision="graph-1")


def test_digest_and_callsite_target_identity_mutations_fail_closed():
    result = certify_why_this_edge(_facts())
    result["target_id"] = "wrong-target"
    assert not verify_why_this_edge(result)
    with pytest.raises(WhyThisEdgeAbstention, match="target_identity"):
        query_why_this_edge(_facts(), expected_callsite_id="other-call")
    facts = _facts()
    facts["digest_sha256"] = hashlib.sha256(b"fake").hexdigest()
    with pytest.raises(WhyThisEdgeAbstention, match="digest"):
        query_why_this_edge(facts)


def test_private_wire_action_returns_typed_result_and_never_shell_fallback(tmp_path):
    action = {
        "tool_call_id": "tc-1",
        "gt_action": {"kind": "why_this_edge", "arguments": _facts()},
    }
    request, result = execute_typed_action_fail_open(action, repo_root=tmp_path)
    assert request["kind"] == "why_this_edge"
    assert result["returncode"] == 0
    assert result["extra"]["interception_decision"] == "REPLACE"
    assert "typed_why_this_edge_exact" in result["output"]
    bad = {"tool_call_id": "tc-2", "gt_action": {"kind": "why_this_edge", "arguments": {}}}
    _, abstained = execute_typed_action_fail_open(bad, repo_root=tmp_path)
    assert abstained["returncode"] == 2
    assert abstained["extra"]["interception_decision"] == "PASS_THROUGH"


def test_receipt_binds_certified_kind_schema_and_digest():
    result = certify_why_this_edge(_facts())
    receipt = why_this_edge_receipt(result)
    assert receipt["kind"] == "why_this_edge"
    assert receipt["explanation_schema"] == "gt.why_this_edge.v1"
    assert receipt["explanation_digest_sha256"] == result["digest_sha256"]


def test_producer_store_publishes_and_queries_by_stable_edge_id(tmp_path):
    store = WhyThisEdgeStore(tmp_path / "why-edge.json")
    facts = _facts()
    receipt = store.publish(facts)
    assert receipt["explanation_digest_sha256"]
    queried = store.query("edge-1", expected_callsite_id="call-1")
    assert queried["target_id"] == "target-a"


def test_shipped_resolution_substrate_harvests_all_rows_and_queries(tmp_path):
    root = Path(__file__).resolve().parents[1]
    rows = harvest_resolution_substrate(root)
    store = WhyThisEdgeStore(tmp_path / "why-edge.json")
    receipts = store.publish_substrate(rows)
    assert len(receipts) == len(rows) == 2
    assert store.query("har10:case-exact-1:edge")["candidate_count"] == 1


def test_red_substrate_digest_mutation_abstains(tmp_path):
    root = Path(__file__).resolve().parents[1]
    source = root / "gt_finalstand" / "receipts" / "har63_resolution_substrate.json"
    target = tmp_path / "gt_finalstand" / "receipts" / source.name
    target.parent.mkdir(parents=True)
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["rows"][0]["target_id"] = "tampered-target"
    target.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(WhyThisEdgeAbstention, match="digest_mismatch"):
        harvest_resolution_substrate(tmp_path)
