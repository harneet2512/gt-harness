from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.arb_evaluate import evaluate


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_evaluator_uses_official_file_line_block_metrics_and_extra_metrics(tmp_path: Path) -> None:
    samples = tmp_path / "samples.jsonl"
    predictions = tmp_path / "arb-shard-0.jsonl"
    _write_jsonl(
        samples,
        [
            {
                "id": "s1",
                "repo": "owner/repo",
                "base_commit": "abc",
                "task_type": "trace2code",
                "gold": {"root_cause_files": ["src.py"]},
                "gold_spans": [{"path": "src.py", "start_line": 2, "end_line": 2}],
                "gold_blocks": [
                    {"path": "src.py", "start_line": 2, "end_line": 2, "kind": "symbol"}
                ],
            }
        ],
    )
    _write_jsonl(
        predictions,
        [
            {
                "sample_id": "s1",
                "repository": "owner/repo",
                "base_commit": "abc",
                "graph_status": "source_backed",
                "abstained": False,
                "ranked_candidates": [
                    {
                        "file_path": "src.py",
                        "symbol": "parse",
                        "confidence": 1.0,
                        "source_span": {"path": "src.py", "start_line": 2, "end_line": 2},
                        "source_text": "return parse(value)",
                    }
                ],
                "delivered_evidence": [],
            }
        ],
    )
    output = tmp_path / "summary.json"
    details = tmp_path / "details.jsonl"
    result = evaluate(
        arb_source=Path("artifacts/final_execution/arb-upstream/src"),
        sample_paths=[samples],
        prediction_paths=[predictions],
        view="ranked",
        output=output,
        details_output=details,
    )
    metrics = result["official_metrics"]["overall"]
    assert metrics["Recall@20"] == 1.0
    assert metrics["line_recall@8k"] == 1.0
    assert metrics["block_recall@8k"] == 1.0
    assert result["task_macro"]["trace2code"]["Any@20"] == 1.0
    assert result["repo_macro"]["owner/repo"]["nDCG@20"] == 1.0


def test_no_gold_rows_do_not_reduce_positive_leaderboard_metrics(tmp_path: Path) -> None:
    samples = tmp_path / "samples.jsonl"
    predictions = tmp_path / "arb-shard-0.jsonl"
    _write_jsonl(
        samples,
        [
            {
                "id": "positive",
                "repo": "owner/repo",
                "base_commit": "abc",
                "task_type": "trace2code",
                "gold": {"root_cause_files": ["src.py"]},
            },
            {
                "id": "negative",
                "repo": "owner/repo",
                "base_commit": "abc",
                "task_type": "selective",
                "gold": {"reason": "no repository evidence required"},
            },
        ],
    )
    _write_jsonl(
        predictions,
        [
            {
                "sample_id": "positive",
                "repository": "owner/repo",
                "ranked_candidates": [
                    {
                        "file_path": "src.py",
                        "line": 1,
                        "source_text": "value = 1",
                    }
                ],
            },
            {
                "sample_id": "negative",
                "repository": "owner/repo",
                "ranked_candidates": [],
                "abstained": True,
            },
        ],
    )

    result = evaluate(
        arb_source=Path("artifacts/final_execution/arb-upstream/src"),
        sample_paths=[samples],
        prediction_paths=[predictions],
        view="ranked",
        output=tmp_path / "summary.json",
        details_output=tmp_path / "details.jsonl",
    )

    assert result["positive_samples"] == 1
    assert result["no_gold_samples"] == 1
    assert result["official_metrics"]["overall"]["Recall@20"] == 1.0


def test_evaluator_reports_top3_latency_and_channel_payload_metrics(tmp_path: Path) -> None:
    samples = tmp_path / "samples.jsonl"
    predictions = tmp_path / "arb-shard-0.jsonl"
    _write_jsonl(
        samples,
        [
            {
                "id": "s1",
                "repo": "owner/repo",
                "base_commit": "abc",
                "task_type": "trace2code",
                "gold": {"root_cause_files": ["gold.py"]},
            }
        ],
    )
    ranked = [
        {"file_path": "wrong-a.py", "line": 1, "source_text": "a"},
        {"file_path": "wrong-b.py", "line": 1, "source_text": "b"},
        {"file_path": "gold.py", "line": 3, "source_text": "gold"},
    ]
    _write_jsonl(
        predictions,
        [
            {
                "sample_id": "s1",
                "repository": "owner/repo",
                "ranked_candidates": ranked,
                "query_latency_ms": 30.0,
                "index_latency_ms": 10.0,
                "payload_chars": 123,
                "retrieval_channels": {
                    "structural": {
                        "candidates": 3,
                        "selected": 1,
                        "payload_chars": 80,
                    }
                },
            }
        ],
    )
    result = evaluate(
        arb_source=Path("artifacts/final_execution/arb-upstream/src"),
        sample_paths=[samples],
        prediction_paths=[predictions],
        view="ranked",
        output=tmp_path / "summary.json",
        details_output=tmp_path / "details.jsonl",
    )
    top3 = result["top3"]["overall"]
    assert top3["precision"] == pytest.approx(1 / 3)
    assert top3["recall"] == 1.0
    assert top3["f1"] == pytest.approx(0.5)
    assert result["latency_ms"]["query"]["p50"] == 30.0
    assert result["latency_ms"]["query"]["p95"] == 30.0
    assert result["channel_contributions"]["structural"]["rows"] == 1
    assert result["payload_chars"]["total"] == 123


def test_channel_metrics_consume_the_actual_hybrid_adapter_receipts() -> None:
    from scripts.arb_evaluate import _channel_summary

    summary = _channel_summary(
        [
            {
                "channel_receipts": [
                    {
                        "channel": "dense",
                        "candidate_count": 12,
                        "available": True,
                        "failed": False,
                        "latency_ms": 8.5,
                        "backend_identity": "snowflake@sha256:abc",
                    },
                    {
                        "channel": "structural",
                        "candidate_count": 3,
                        "available": True,
                        "failed": False,
                        "latency_ms": 0.5,
                    },
                ],
                "ranked_candidates": [
                    {
                        "channel_ranks": [
                            {"channel": "dense", "rank": 1},
                            {"channel": "structural", "rank": 1},
                        ]
                    }
                ],
                "delivered_evidence": [
                    {
                        "channel_ranks": [
                            {"channel": "dense", "rank": 1},
                            {"channel": "structural", "rank": 1},
                        ]
                    }
                ],
            }
        ]
    )

    assert summary["dense"]["candidate_count"] == 12
    assert summary["dense"]["ranked_file_count"] == 1
    assert summary["dense"]["selected_count"] == 1
    assert summary["dense"]["backend_identities"] == ["snowflake@sha256:abc"]
    assert summary["structural"]["candidate_count"] == 3


def test_partial_selective_evaluation_receipts_insufficient_fold_balance(tmp_path):
    from scripts.arb_evaluate import _run_selective_cv

    def imbalanced(*args, **kwargs):
        del args, kwargs
        raise ValueError("fold 0 lacks a class after repo grouping")

    receipt = _run_selective_cv(
        imbalanced,
        selective_path=tmp_path / "selective.jsonl",
        output=tmp_path / "summary.json",
        allow_incomplete=True,
    )

    assert receipt["status"] == "insufficient_class_balance"
    assert "lacks a class" in receipt["reason"]

    with pytest.raises(ValueError, match="lacks a class"):
        _run_selective_cv(
            imbalanced,
            selective_path=tmp_path / "selective.jsonl",
            output=tmp_path / "summary.json",
            allow_incomplete=False,
        )


def test_evaluator_rejects_missing_and_extra_prediction_ids(tmp_path: Path) -> None:
    samples = tmp_path / "samples.jsonl"
    predictions = tmp_path / "arb-shard-0.jsonl"
    _write_jsonl(
        samples,
        [
            {
                "id": "expected",
                "repo": "owner/repo",
                "base_commit": "abc",
                "task_type": "trace2code",
                "gold": {"root_cause_files": ["src.py"]},
            }
        ],
    )
    _write_jsonl(
        predictions,
        [{"sample_id": "extra", "repository": "owner/repo", "ranked_candidates": []}],
    )
    with pytest.raises(ValueError, match="prediction ID set mismatch"):
        evaluate(
            arb_source=Path("artifacts/final_execution/arb-upstream/src"),
            sample_paths=[samples],
            prediction_paths=[predictions],
            view="ranked",
            output=tmp_path / "summary.json",
            details_output=tmp_path / "details.jsonl",
        )


def test_evaluator_can_publish_missing_id_progress_without_accepting_extras(tmp_path: Path) -> None:
    samples = tmp_path / "samples.jsonl"
    predictions = tmp_path / "arb-shard-0.jsonl"
    _write_jsonl(
        samples,
        [
            {
                "id": "present",
                "repo": "owner/repo",
                "base_commit": "abc",
                "gold": {"root_cause_files": ["src.py"]},
            },
            {
                "id": "missing",
                "repo": "owner/repo",
                "base_commit": "abc",
                "gold": {"root_cause_files": ["src.py"]},
            },
        ],
    )
    _write_jsonl(
        predictions,
        [
            {
                "sample_id": "present",
                "repository": "owner/repo",
                "ranked_candidates": [{"file_path": "src.py", "line": 1}],
            }
        ],
    )
    result = evaluate(
        arb_source=Path("artifacts/final_execution/arb-upstream/src"),
        sample_paths=[samples],
        prediction_paths=[predictions],
        view="ranked",
        output=tmp_path / "summary.json",
        details_output=tmp_path / "details.jsonl",
        allow_incomplete=True,
    )
    assert result["complete"] is False
    assert result["missing_sample_ids"] == ["missing"]
