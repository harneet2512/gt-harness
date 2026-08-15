#!/usr/bin/env python3
"""Evaluate GT ARB receipts with the pinned official metric implementations.

This is an offline evaluator.  It joins gold labels only after GT prediction
receipts are complete, then delegates file/line/block and BCY calculations to
the pinned ARB source.  It also reports binary Any@K, binary nDCG@K, and
task/repository macro views so a high sample count cannot hide a repository
or workflow failure.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from statistics import mean
from typing import Any


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{line_number}: expected object")
        rows.append(row)
    return rows


def _prediction_paths(root: Path, explicit: Iterable[Path]) -> list[Path]:
    paths = [path for path in explicit if path.exists()]
    if not paths:
        paths = sorted(root.rglob("arb-shard-*.jsonl"))
    if not paths:
        raise FileNotFoundError(f"no ARB shard JSONL files under {root}")
    return paths


def _unique_paths(rows: Iterable[dict[str, Any]]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for row in rows:
        path = str(row.get("file_path") or row.get("path") or "").replace("\\", "/")
        if path and path not in seen:
            output.append(path)
            seen.add(path)
    return output


def _chunks(prediction: dict[str, Any], view: str) -> list[dict[str, Any]]:
    values = prediction.get("ranked_candidates") or []
    if view == "delivered":
        values = prediction.get("delivered_evidence") or []
    chunks: list[dict[str, Any]] = []
    for index, row in enumerate(values, 1):
        if not isinstance(row, dict):
            continue
        span = row.get("source_span") or {}
        chunk = {
            "chunk_id": f"gt:{prediction.get('sample_id')}:{index}",
            "path": str(row.get("file_path") or span.get("path") or "").replace("\\", "/"),
            "kind": "symbol" if row.get("symbol") else "file",
            "symbol": str(row.get("symbol") or ""),
            "start_line": int(span.get("start_line") or row.get("line") or 0),
            "end_line": int(span.get("end_line") or span.get("start_line") or row.get("line") or 0),
            "text": str(
                row.get("source_text") or (row.get("source_chunk") or {}).get("text") or ""
            ),
        }
        if chunk["path"] and chunk["start_line"] > 0:
            chunk["end_line"] = max(chunk["start_line"], chunk["end_line"])
        chunks.append(chunk)
    return chunks


def _ndcg(gold: set[str], ranked_paths: list[str], k: int) -> float:
    if not gold or k <= 0:
        return 0.0
    dcg = sum(
        1.0 / math.log2(index + 2) for index, path in enumerate(ranked_paths[:k]) if path in gold
    )
    ideal = sum(1.0 / math.log2(index + 2) for index in range(min(k, len(gold))))
    return dcg / ideal if ideal else 0.0


def _extra_metrics(gold_files: list[str], chunks: list[dict[str, Any]]) -> dict[str, float]:
    gold = set(gold_files)
    ranked = _unique_paths(chunks)
    return {f"Any@{k}": float(bool(gold & set(ranked[:k]))) for k in (1, 5, 10, 20)} | {
        f"nDCG@{k}": _ndcg(gold, ranked, k) for k in (5, 10, 20)
    }


def _top3(gold_files: list[str], chunks: list[dict[str, Any]]) -> dict[str, float]:
    gold = set(gold_files)
    ranked = _unique_paths(chunks)[:3]
    hits = len(gold & set(ranked))
    precision = hits / len(ranked) if ranked else 0.0
    recall = hits / len(gold) if gold else 0.0
    f1 = (2.0 * precision * recall / (precision + recall)) if precision + recall else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "gold_hits": float(hits),
        "predicted_files": float(len(ranked)),
    }


def _percentiles(values: Iterable[float]) -> dict[str, float | int]:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return {"count": 0, "p50": 0.0, "p95": 0.0, "p99": 0.0}

    def nearest_rank(percentile: float) -> float:
        index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
        return ordered[index]

    return {
        "count": len(ordered),
        "p50": nearest_rank(0.50),
        "p95": nearest_rank(0.95),
        "p99": nearest_rank(0.99),
    }


def _latency_summary(predictions: Iterable[dict[str, Any]]) -> dict[str, dict[str, float | int]]:
    rows = list(predictions)
    query_values = [
        float(row["query_latency_ms"]) for row in rows if row.get("query_latency_ms") is not None
    ]
    index_values = [
        float(row["index_latency_ms"]) for row in rows if row.get("index_latency_ms") is not None
    ]
    total_values = [
        float(row.get("query_latency_ms") or 0.0) + float(row.get("index_latency_ms") or 0.0)
        for row in rows
        if row.get("query_latency_ms") is not None or row.get("index_latency_ms") is not None
    ]
    return {
        "query": _percentiles(query_values),
        "index": _percentiles(index_values),
        "total": _percentiles(total_values),
    }


def _channel_summary(predictions: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for prediction in predictions:
        channels = (
            prediction.get("retrieval_channels") or prediction.get("channel_contributions") or {}
        )
        if isinstance(channels, dict) and channels:
            channel_items = channels.items()
        else:
            channel_items = (
                (str(receipt.get("channel") or "unknown"), receipt)
                for receipt in prediction.get("channel_receipts") or ()
                if isinstance(receipt, dict)
            )
        for channel, value in channel_items:
            row = summary.setdefault(
                str(channel),
                {
                    "rows": 0,
                    "candidate_count": 0,
                    "ranked_file_count": 0,
                    "selected_count": 0,
                    "payload_chars": 0,
                    "payload_tokens": 0,
                    "available_rows": 0,
                    "failed_rows": 0,
                    "latency_ms_total": 0.0,
                    "backend_identities": [],
                },
            )
            row["rows"] = int(row["rows"]) + 1
            if isinstance(value, dict):
                row["candidate_count"] = int(row["candidate_count"]) + int(
                    value.get("candidates") or value.get("candidate_count") or 0
                )
                row["selected_count"] = int(row["selected_count"]) + int(
                    value.get("selected") or value.get("selected_count") or 0
                )
                row["payload_chars"] = int(row["payload_chars"]) + int(
                    value.get("payload_chars") or value.get("chars") or 0
                )
                row["payload_tokens"] = int(row["payload_tokens"]) + int(
                    value.get("payload_tokens") or value.get("tokens") or 0
                )
                row["available_rows"] = int(row["available_rows"]) + int(
                    bool(value.get("available", True))
                )
                row["failed_rows"] = int(row["failed_rows"]) + int(bool(value.get("failed")))
                row["latency_ms_total"] = float(row["latency_ms_total"]) + float(
                    value.get("latency_ms") or 0.0
                )
                identity = str(value.get("backend_identity") or "")
                if identity and identity not in row["backend_identities"]:
                    row["backend_identities"].append(identity)
            elif isinstance(value, list):
                row["candidate_count"] = int(row["candidate_count"]) + len(value)
        for candidate_key, contribution_key in (
            ("ranked_candidates", "ranked_file_count"),
            ("delivered_evidence", "selected_count"),
        ):
            for candidate in prediction.get(candidate_key) or ():
                if not isinstance(candidate, dict):
                    continue
                channel_names = {
                    str(item.get("channel") or "")
                    for item in candidate.get("channel_ranks") or ()
                    if isinstance(item, dict)
                }
                for channel in channel_names:
                    if channel not in summary:
                        continue
                    summary[channel][contribution_key] = int(summary[channel][contribution_key]) + 1
    for row in summary.values():
        row["backend_identities"] = sorted(row["backend_identities"])
        row["latency_ms_mean"] = (
            float(row["latency_ms_total"]) / int(row["rows"]) if int(row["rows"]) else 0.0
        )
    return summary


def _group_top3(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, float]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(key) or "unknown")].append(row)
    return {
        group: {
            metric: mean(float(row["top3"][metric]) for row in members)
            for metric in ("precision", "recall", "f1", "gold_hits", "predicted_files")
        }
        for group, members in groups.items()
    }


def _payload_summary(predictions: Iterable[dict[str, Any]], key: str) -> dict[str, float | int]:
    values = [
        float(prediction.get(key) or 0.0)
        for prediction in predictions
        if prediction.get(key) is not None
    ]
    return {
        "count": len(values),
        "total": sum(values),
        "mean": mean(values) if values else 0.0,
        "max": max(values) if values else 0.0,
    }


def _run_selective_cv(
    reporter: Any,
    *,
    selective_path: Path,
    output: Path,
    allow_incomplete: bool,
) -> dict[str, Any]:
    try:
        return reporter(
            {"GT": selective_path},
            output.with_name(output.stem + ".selective.md"),
            output.with_name(output.stem + ".selective.json"),
            fold_count=5,
        )
    except ValueError as exc:
        if not allow_incomplete:
            raise
        return {
            "status": "insufficient_class_balance",
            "reason": str(exc),
        }


def _macro(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, float]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(key) or "unknown")].append(row)
    output: dict[str, dict[str, float]] = {}
    for group, members in groups.items():
        output[group] = {
            "Recall@20": mean(
                float((row.get("metrics") or {}).get("Recall@20") or 0.0) for row in members
            ),
            "MRR": mean(float((row.get("metrics") or {}).get("MRR") or 0.0) for row in members),
            "Any@20": mean(
                float((row.get("extra_metrics") or {}).get("Any@20") or 0.0) for row in members
            ),
            "nDCG@20": mean(
                float((row.get("extra_metrics") or {}).get("nDCG@20") or 0.0) for row in members
            ),
            "BCY@8000": mean(
                float((row.get("bcy") or {}).get("BCY@8000") or 0.0) for row in members
            ),
        }
    return output


def _absolute_manifest(path: Path) -> dict[tuple[str, str], Path]:
    manifest: dict[tuple[str, str], Path] = {}
    for row in _load_jsonl(path):
        if row.get("status") not in (None, "ok"):
            continue
        chunks = Path(str(row.get("chunks_path") or ""))
        if not chunks:
            continue
        if not chunks.is_absolute():
            parts = chunks.parts
            if parts and parts[0] == "data":
                chunks = path.parents[2] / Path(*parts[1:])
            else:
                chunks = path.parent / chunks
        manifest[(str(row.get("repo") or ""), str(row.get("base_commit") or ""))] = chunks.resolve()
    return manifest


def evaluate(
    *,
    arb_source: Path,
    sample_paths: list[Path],
    prediction_paths: list[Path],
    view: str,
    output: Path,
    details_output: Path,
    corpus_manifest: Path | None = None,
    selective_details_output: Path | None = None,
    allow_incomplete: bool = False,
) -> dict[str, Any]:
    sys.path.insert(0, str(arb_source))
    from agent_retrieval_bench.baseline import (  # type: ignore[import-not-found]
        block_metrics_at_budget,
        gold_blocks,
        gold_spans,
        hard_negative_files,
        line_metrics_at_budget,
        sample_metrics,
        summarize_details,
        target_gold_files,
    )
    from agent_retrieval_bench.bcy_curve import (  # type: ignore[import-not-found]
        CorpusFileCache,
        evaluate_run,
        evaluate_sample,
    )
    from agent_retrieval_bench.selective_cv import (
        report_selective_group_cv,  # type: ignore[import-not-found]
    )

    gold_by_id: dict[str, dict[str, Any]] = {}
    for path in sample_paths:
        for sample in _load_jsonl(path):
            sample_id = str(sample.get("id") or "")
            if not sample_id:
                raise ValueError(f"missing sample id in {path}")
            if sample_id in gold_by_id:
                raise ValueError(f"duplicate gold sample id: {sample_id}")
            gold_by_id[sample_id] = sample

    prediction_by_id: dict[str, dict[str, Any]] = {}
    for path in prediction_paths:
        for prediction in _load_jsonl(path):
            sample_id = str(prediction.get("sample_id") or "")
            if not sample_id:
                raise ValueError(f"missing prediction sample_id in {path}")
            if sample_id in prediction_by_id:
                raise ValueError(f"duplicate prediction sample_id: {sample_id}")
            prediction_by_id[sample_id] = prediction

    gold_ids = set(gold_by_id)
    prediction_ids = set(prediction_by_id)
    missing_ids = sorted(gold_ids - prediction_ids)
    extra_ids = sorted(prediction_ids - gold_ids)
    if extra_ids:
        raise ValueError(
            f"prediction ID set mismatch: missing={missing_ids[:5]} extra={extra_ids[:5]}"
        )
    if missing_ids and not allow_incomplete:
        raise ValueError(
            f"prediction ID set mismatch: missing={missing_ids[:5]} extra={extra_ids[:5]}"
        )

    details: list[dict[str, Any]] = []
    selective_details: list[dict[str, Any]] = []
    for sample_id in sorted(prediction_by_id):
        if sample_id not in gold_by_id:
            if allow_incomplete:
                continue
            raise ValueError(f"prediction has no matching ARB gold row: {sample_id}")
        sample = gold_by_id[sample_id]
        prediction = prediction_by_id[sample_id]
        if str(sample.get("repo") or "") != str(prediction.get("repository") or ""):
            raise ValueError(f"repository mismatch for {sample_id}")
        chunks = _chunks(prediction, view)
        gold_files = target_gold_files(sample)
        metrics = (
            sample_metrics(gold_files, chunks, hard_negative_files=hard_negative_files(sample))
            if gold_files
            else {}
        )
        detail = {
            "sample_id": sample_id,
            "task_type": sample.get("task_type"),
            "repo": sample.get("repo"),
            "base_commit": sample.get("base_commit"),
            "gold_files": gold_files,
            "gold_spans": gold_spans(sample),
            "gold_blocks": gold_blocks(sample),
            "hard_negative_files": hard_negative_files(sample),
            "top_files": _unique_paths(chunks)[:20],
            "ranked_chunks": chunks,
            "metrics": metrics,
            "extra_metrics": _extra_metrics(gold_files, chunks),
            "graph_status": prediction.get("graph_status"),
            "abstained": bool(prediction.get("abstained")),
            "abstention_reason": prediction.get("abstention_reason"),
            "query_latency_ms": prediction.get("query_latency_ms"),
            "index_latency_ms": prediction.get("index_latency_ms"),
            "payload_chars": prediction.get("payload_chars")
            or prediction.get("payload_size_chars"),
            "payload_tokens": prediction.get("payload_tokens")
            or prediction.get("payload_size_tokens"),
        }
        detail["top3"] = _top3(gold_files, chunks)
        if detail["gold_spans"]:
            detail["line_metrics"] = line_metrics_at_budget(detail["gold_spans"], chunks)
        if detail["gold_blocks"]:
            detail["block_metrics"] = block_metrics_at_budget(detail["gold_blocks"], chunks)
        details.append(detail)
        top_candidate = (prediction.get("ranked_candidates") or [{}])[0]
        selective_details.append(
            {
                "sample_id": sample_id,
                "task_type": sample.get("task_type"),
                "repo": sample.get("repo"),
                "base_commit": sample.get("base_commit"),
                "label": "positive" if gold_files else "no_gold",
                "no_gold_reason": (sample.get("gold") or {}).get("reason")
                if not gold_files
                else None,
                "gold_files": gold_files,
                "confidence": float(
                    top_candidate.get("selection_confidence")
                    or top_candidate.get("fusion_score")
                    or top_candidate.get("retrieval_relevance")
                    or top_candidate.get("confidence")
                    or 0.0
                ),
                "metrics": metrics,
                "top_files": _unique_paths(chunks)[:20],
            }
        )

    positive_details = [row for row in details if row["gold_files"]]
    bcy_result: dict[str, Any] = {}
    if corpus_manifest and corpus_manifest.exists():
        manifest = _absolute_manifest(corpus_manifest)
        cache = CorpusFileCache(manifest)
        budgets = (4000, 8000, 16000, 32000)
        bcy_result = evaluate_run(
            "GT",
            "hybrid deterministic retrieval",
            details_output,
            positive_details,
            cache,
            budgets,
        )
        for row in positive_details:
            bcy_row = evaluate_sample(row, row["gold_files"], cache, budgets)
            row["bcy"] = {f"BCY@{budget}": bcy_row["packed"][budget]["bcy"] for budget in budgets}

    selective_path = selective_details_output or output.with_name(
        output.stem + ".selective_details.jsonl"
    )
    if selective_details:
        selective_path.parent.mkdir(parents=True, exist_ok=True)
        selective_path.write_text(
            "\n".join(json.dumps(row, sort_keys=True) for row in selective_details) + "\n",
            encoding="utf-8",
        )
    selective_result: dict[str, Any] = {}
    labels = {str(row["label"]) for row in selective_details}
    selective_repos = {str(row["repo"]) for row in selective_details}
    if {"positive", "no_gold"} <= labels and len(selective_repos) >= 5:
        selective_result = _run_selective_cv(
            report_selective_group_cv,
            selective_path=selective_path,
            output=output,
            allow_incomplete=allow_incomplete,
        )
    elif {"positive", "no_gold"} <= labels:
        selective_result = {
            "status": "insufficient_repository_groups",
            "repositories": len(selective_repos),
            "required_repositories": 5,
        }

    summary = {
        "schema": "gt.arb.evaluation.v1",
        "view": view,
        "samples": len(details),
        "gold_rows": len(gold_by_id),
        "prediction_rows": len(prediction_by_id),
        "complete": not missing_ids and not extra_ids,
        "missing_sample_ids": missing_ids,
        "extra_prediction_ids": extra_ids,
        # Positive retrieval and selective abstention are different ARB
        # evaluation surfaces.  No-gold rows must never be averaged as zero
        # retrieval scores in the positive leaderboard.
        "positive_samples": len(positive_details),
        "no_gold_samples": len(details) - len(positive_details),
        "official_metrics": summarize_details(positive_details),
        "bcy": bcy_result,
        "task_macro": _macro(positive_details, "task_type"),
        "repo_macro": _macro(positive_details, "repo"),
        "top3": {
            "overall": {
                metric: mean(float(row["top3"][metric]) for row in positive_details)
                if positive_details
                else 0.0
                for metric in ("precision", "recall", "f1", "gold_hits", "predicted_files")
            },
            "task_macro": _group_top3(positive_details, "task_type"),
            "repo_macro": _group_top3(positive_details, "repo"),
        },
        "latency_ms": _latency_summary(prediction_by_id.values()),
        "channel_contributions": _channel_summary(prediction_by_id.values()),
        "payload_chars": _payload_summary(prediction_by_id.values(), "payload_chars"),
        "payload_tokens": _payload_summary(prediction_by_id.values(), "payload_tokens"),
        "selective_group_cv": selective_result,
        "graph_status_counts": {
            status: sum(str(row.get("graph_status") or "") == status for row in details)
            for status in sorted({str(row.get("graph_status") or "") for row in details})
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    details_output.parent.mkdir(parents=True, exist_ok=True)
    details_output.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in details) + "\n", encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arb-source", type=Path, required=True)
    parser.add_argument("--samples", type=Path, action="append", required=True)
    parser.add_argument("--predictions-root", type=Path)
    parser.add_argument("--predictions", type=Path, action="append", default=[])
    parser.add_argument("--view", choices=("ranked", "delivered"), default="ranked")
    parser.add_argument("--corpus-manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--details-output", type=Path, required=True)
    parser.add_argument("--selective-details-output", type=Path)
    parser.add_argument("--allow-incomplete", action="store_true")
    args = parser.parse_args()
    if not args.predictions_root and not args.predictions:
        parser.error("one of --predictions-root or --predictions is required")
    root = args.predictions_root or Path(".")
    paths = _prediction_paths(root, args.predictions)
    result = evaluate(
        arb_source=args.arb_source,
        sample_paths=args.samples,
        prediction_paths=paths,
        view=args.view,
        corpus_manifest=args.corpus_manifest,
        output=args.output,
        details_output=args.details_output,
        selective_details_output=args.selective_details_output,
        allow_incomplete=args.allow_incomplete,
    )
    print(
        json.dumps(
            {
                "samples": result["samples"],
                "complete": result["complete"],
                "output": str(args.output),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
