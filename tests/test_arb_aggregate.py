from __future__ import annotations

import json

import pytest

from scripts.arb_aggregate import aggregate


def test_aggregate_reads_only_shard_files(tmp_path) -> None:
    (tmp_path / "arb-shard-0.jsonl").write_text(
        json.dumps(
            {
                "sample_id": "b",
                "abstained": True,
                "delivered_evidence": [],
                "ranked_candidates": [],
                "graph_status": "index_unavailable",
                "index_error_type": "MissingRuntime",
                "abstention_reason": "index_unavailable",
                "index_latency_ms": 2,
                "query_latency_ms": 1,
                "phase_latency_ms": {
                    "repository_prepare_ms": 0.25,
                    "retrieval_ms": 0.75,
                },
                "channel_receipts": [{"channel": "dense", "latency_ms": 0.5, "candidate_count": 2}],
                "dense_backend_receipt": {
                    "document_cache_hits_delta": 1,
                    "document_cache_misses_delta": 2,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "arb-shard-1.jsonl").write_text(
        json.dumps(
            {
                "sample_id": "a",
                "abstained": False,
                "delivered_evidence": [{"path": "src/a.py"}],
                "ranked_candidates": [{"path": "src/a.py"}],
                "graph_status": "source_backed",
                "index_error_type": None,
                "abstention_reason": None,
                "index_latency_ms": 4,
                "query_latency_ms": 3,
                "phase_latency_ms": {
                    "repository_prepare_ms": 1.0,
                    "retrieval_ms": 2.0,
                },
                "channel_receipts": [{"channel": "dense", "latency_ms": 1.5, "candidate_count": 3}],
                "dense_backend_receipt": {
                    "document_cache_hits_delta": 4,
                    "document_cache_misses_delta": 1,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    result = aggregate(tmp_path, expected_rows=2)
    assert result["rows"] == 2
    assert result["complete"] is True
    assert result["delivered_rows"] == 1
    assert result["graph_status_counts"] == {"index_unavailable": 1, "source_backed": 1}
    assert result["query_latency_ms"]["p50"] == 1.0
    assert result["query_latency_ms"]["p95"] == 3.0
    assert result["phase_latency_ms"]["retrieval_ms"]["total"] == 2.75
    assert result["channel_metrics"]["dense"]["candidate_count"] == 5
    assert result["dense_cache_deltas"]["document_cache_hits_delta"] == 5


def test_aggregate_can_publish_partial_progress(tmp_path) -> None:
    (tmp_path / "arb-shard-0.jsonl").write_text(
        json.dumps({"sample_id": "only", "abstained": True}) + "\n",
        encoding="utf-8",
    )
    result = aggregate(tmp_path, expected_rows=2, allow_incomplete=True)
    assert result["complete"] is False
    assert result["rows"] == 1


def test_aggregate_reports_shard_inventory_and_missing_ids(tmp_path) -> None:
    (tmp_path / "arb-shard-3.jsonl").write_text(
        json.dumps({"sample_id": "a", "abstained": True}) + "\n",
        encoding="utf-8",
    )
    expected = tmp_path / "expected.jsonl"
    expected.write_text(
        json.dumps({"sample_id": "a"}) + "\n" + json.dumps({"sample_id": "b"}) + "\n",
        encoding="utf-8",
    )
    result = aggregate(tmp_path, expected_rows=2, expected_samples=expected, allow_incomplete=True)
    assert result["shards_seen"] == 1
    assert result["shard_rows"] == {"arb-shard-3.jsonl": 1}
    assert result["missing_sample_ids"] == ["b"]


def test_aggregate_rejects_extra_sample_ids_even_for_partial_progress(tmp_path) -> None:
    (tmp_path / "arb-shard-0.jsonl").write_text(
        json.dumps({"sample_id": "a", "abstained": True})
        + "\n"
        + json.dumps({"sample_id": "unexpected", "abstained": True})
        + "\n",
        encoding="utf-8",
    )
    expected = tmp_path / "expected.jsonl"
    expected.write_text(json.dumps({"sample_id": "a"}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="extra sample ids"):
        aggregate(tmp_path, expected_rows=1, expected_samples=expected, allow_incomplete=True)
