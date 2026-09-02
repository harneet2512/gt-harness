from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

from gt_harness.runtime_receipts import issue_runtime_receipts, verify_runtime_receipt


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_successful_miniswe_run_issues_bound_product_and_adapter_receipts(
    tmp_path: Path,
) -> None:
    state = tmp_path / "gt-state"
    task_state = state / "task-hash"
    trajectory = tmp_path / "miniswe_trajectory.json"
    report = tmp_path / "miniswe_report.json"
    product = tmp_path / "gt-run.json"
    adapter = tmp_path / "benchmark-adapter.json"
    _write_json(
        trajectory,
        {
            "messages": [{"role": "assistant", "content": "done"}],
            "info": {
                "model_stats": {"api_calls": 3},
                "exit_status": "Submitted",
            },
        },
    )
    _write_json(
        report,
        {
            "model": "meta/muse-spark-1.2-contributor",
            "terminal": "submitted_unverified",
            "exit_code": 0,
            "gt_mode": "advisory",
            "research_valid": True,
            "gt": {
                "contract_shipped": True,
                "terminal_requests": 3,
                "delivered_evidence": 1,
                "requested_model": "meta/muse-spark-1.2-contributor",
                "provider_reported_model": "meta/muse-spark-1.2-contributor",
                "resolved_model": "openai/meta/muse-spark-1.2-contributor",
                "event_journal": {"event_count": 3, "event_head": "a" * 64},
                "usage": {
                    "prompt_tokens": 40,
                    "completion_tokens": 5,
                    "prompt_cache_hit_tokens": 11,
                },
                "verified": False,
                "unmet_predicates": ["pred-1"],
                "unverified_predicates": ["pred-1"],
            },
        },
    )
    events = [
        {
            "event": "context_addition_delivery",
            "event_hash": "1" * 64,
            "sequence": 1,
            "lane": "prompt",
            "kind": "context_contract",
            "evidence_type": "context_contract",
            "dedup_key": "prompt-contract-1",
            "target": "provider_prompt",
            "payload_sha256": "a" * 64,
            "action_index": 0,
            "iteration": 0,
            "rendered_bytes": 100,
        },
        {
            "event": "receipt",
            "event_hash": "2" * 64,
            "sequence": 2,
            "transition": "delivered",
            "dedup_key": "prompt-contract-1",
            "evidence_type": "context_contract",
            "iteration": 0,
            "payload_hash": "a" * 64,
        },
        {
            "event": "provider_delivery",
            "event_hash": "3" * 64,
            "sequence": 3,
            "iteration": 1,
            "request_id": "request-1",
        },
        {
            "event": "provider_response",
            "event_hash": "4" * 64,
            "sequence": 4,
            "usage": {
                "prompt_tokens": 17,
                "completion_tokens": 2,
                "prompt_tokens_details": {"cached_tokens": 4},
                "cost": 0.01,
            },
        },
        {
            "event": "evidence_delivery",
            "event_hash": "5" * 64,
            "sequence": 5,
            "lane": "sealed",
            "kind": "localization",
            "evidence_type": "localization",
            "dedup_key": "localization-1",
            "artifact_sha256": "c" * 64,
            "payload_sha256": "9" * 64,
            "action_index": 0,
            "iteration": 1,
            "rendered_bytes": 123,
        },
        {
            "event": "receipt",
            "event_hash": "6" * 64,
            "sequence": 6,
            "transition": "delivered",
            "dedup_key": "localization-1",
            "evidence_type": "localization",
            "iteration": 1,
            "payload_hash": "9" * 64,
        },
        {
            "event": "provider_delivery",
            "event_hash": "7" * 64,
            "sequence": 7,
            "iteration": 2,
            "request_id": "request-2",
        },
        {
            "event": "provider_response",
            "event_hash": "8" * 64,
            "sequence": 8,
            "usage": {
                "prompt_tokens": 23,
                "completion_tokens": 3,
                "prompt_tokens_details": {"cached_tokens": 7},
                "cost": 0.02,
            },
        },
        {
            "event": "delivery_refused",
            "event_hash": "0" * 64,
            "sequence": 9,
            "lane": "prompt",
            "kind": "context_delta",
            "dedup_key": "prompt-delta-refused",
            "reason": "delivery_byte_ceiling",
            "candidate_ordinal": 3,
            "rendered_bytes": 1_401,
            "payload_sha256": "f" * 64,
            "admitted_count": 2,
            "admitted_bytes": 223,
        },
        {
            "event": "dense_index_ready",
            "event_hash": "b" * 64,
            "sequence": 10,
            "query_ready": True,
            "model_sha256": "5" * 64,
            "tokenizer_sha256": "4" * 64,
            "dimension": 768,
            "document_count": 4,
            "query_result_count": 4,
            "index_sha256": "3" * 64,
        },
        {
            "event": "session_closed",
            "event_hash": "d" * 64,
            "sequence": 11,
            "terminal": "submitted_unverified",
        },
    ]
    task_state.mkdir(parents=True)
    (task_state / "events.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in events), encoding="utf-8"
    )
    _write_json(
        task_state / "reproducibility_manifest.json",
        {
            "schema": "gt.repro.v1",
            "research_valid": True,
            "provider_receipts": {"request_count": 3, "valid": True},
            "model": {"match": True},
            "event_journal": {
                "event_count": 11,
                "event_head": "d" * 64,
                "valid": True,
                "issues": [],
            },
        },
    )
    graph_state = state / "repo-hash"
    _write_json(
        graph_state / "graph.manifest.json",
        {
            "schema": "gt.graph_certification.v1",
            "binary_certified": True,
            "graph_sha256": "e" * 64,
            "sqlite_quick_check": "ok",
        },
    )

    issued = issue_runtime_receipts(
        report_path=report,
        trajectory_path=trajectory,
        state_dir=state,
        product_receipt_path=product,
        adapter_receipt_path=adapter,
        task_id="task-a",
        product_source_sha="f" * 40,
        treatment="groundtruth",
        requested_model="meta/muse-spark-1.2-contributor",
        scaffold_version="2.4.6",
        time_budget_seconds=3600,
    )

    product_row = json.loads(product.read_text(encoding="utf-8"))
    adapter_row = json.loads(adapter.read_text(encoding="utf-8"))
    copied = product.with_name("gt-run.trajectory.json")
    assert issued == product_row
    assert product_row["schema"] == "gt.run_receipt.v1"
    assert product_row["status"] == "COMPLETED"
    assert product_row["provider_calls"] == 3
    assert product_row["provider_completed_calls"] == 2
    assert product_row["provider_failed_calls"] == 1
    assert product_row["input_tokens"] == 40
    assert product_row["cached_tokens"] == 11
    assert product_row["output_tokens"] == 5
    assert product_row["total_cost"] == 0.03
    assert product_row["treatment_receipt"]["schema"] == "gt.miniswe_treatment_receipt.v1"
    treatment = product_row["treatment_receipt"]
    assert treatment["delivery_count"] == 2
    assert treatment["prompt_delivery_count"] == 1
    assert treatment["sealed_delivery_count"] == 1
    assert treatment["prompt_context_deliveries"][0]["kind"] == "context_contract"
    assert treatment["evidence_deliveries"][0]["event_hash"] == "5" * 64
    assert treatment["refused_deliveries"][0]["reason"] == "delivery_byte_ceiling"
    assert treatment["delivery_budget"] == {
        "unit": "utf8_bytes",
        "conversion_from_legacy_tokens": "4_bytes_per_token",
        "sealed_limit": 1_400,
        "prompt_contract_limit": 2_000,
        "prompt_delta_limit": 1_400,
        "total_limit": 9_600,
        "total_observed": 223,
        "task_delivery_limit": 24,
        "admitted_count": 2,
        "refused_count": 1,
    }
    assert product_row["treatment_receipt"]["graph_certification"]["graph_sha256"] == "e" * 64
    assert product_row["integrity"]["trajectory_sha256"] == hashlib.sha256(
        trajectory.read_bytes()
    ).hexdigest()
    assert copied.read_bytes() == trajectory.read_bytes()
    assert adapter_row == {
        "schema": "gt.benchmark_adapter_receipt.v1",
        "task_id": "task-a",
        "product_command": "gt-miniswe-run",
        "attempt": 1,
        "treatment": "groundtruth",
        "requested_model": "meta/muse-spark-1.2-contributor",
        "effective_model": "openai/meta/muse-spark-1.2-contributor",
        "agent_scaffold_version": "2.4.6",
        "product_source_sha": "f" * 40,
        "time_budget_seconds": 3600,
    }
    assert verify_runtime_receipt(product) == []

    mutations = {
        "treatment_delivery_limit_exceeded": lambda row: row["treatment_receipt"][
            "provider_delivery_receipts"
        ].extend(deepcopy(row["treatment_receipt"]["provider_delivery_receipts"]) * 12),
        "treatment_delivery_late": lambda row: row["treatment_receipt"][
            "provider_delivery_receipts"
        ][0].update(same_observation=False),
        "treatment_delivery_context_budget_exceeded": lambda row: row[
            "treatment_receipt"
        ]["provider_delivery_receipts"][1].update(context_byte_count=1_401),
        "treatment_prompt_context_budget_exceeded": lambda row: row[
            "treatment_receipt"
        ]["provider_delivery_receipts"][0].update(delivery_kind="localization"),
        "treatment_duplicate_delivery_identity": lambda row: row[
            "treatment_receipt"
        ]["provider_delivery_receipts"][1].update(
            context_sha256=row["treatment_receipt"]["provider_delivery_receipts"][0][
                "context_sha256"
            ],
            delivery_identity=row["treatment_receipt"]["provider_delivery_receipts"][0][
                "delivery_identity"
            ],
        ),
        "treatment_delivery_refusal_invalid": lambda row: row[
            "treatment_receipt"
        ]["refused_deliveries"][0].update(dedup_key="prompt-contract-1"),
        "treatment_dense_index_not_ready": lambda row: row["treatment_receipt"][
            "dense_index_receipt"
        ].update(query_ready=False),
    }
    for expected_error, mutate in mutations.items():
        changed = deepcopy(product_row)
        mutate(changed)
        _write_json(product, changed)
        assert expected_error in verify_runtime_receipt(product)


def test_runtime_receipt_rejects_provider_count_disagreement(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    trajectory = tmp_path / "trajectory.json"
    _write_json(
        report,
        {
            "exit_code": 0,
            "terminal": "submitted_unverified",
            "research_valid": True,
            "gt": {"terminal_requests": 3, "usage": {}},
        },
    )
    _write_json(trajectory, {"messages": [], "info": {"model_stats": {"api_calls": 2}}})

    try:
        issue_runtime_receipts(
            report_path=report,
            trajectory_path=trajectory,
            state_dir=tmp_path / "state",
            product_receipt_path=tmp_path / "gt-run.json",
            adapter_receipt_path=tmp_path / "adapter.json",
            task_id="task-a",
            product_source_sha="f" * 40,
            treatment="groundtruth",
            requested_model="meta/muse-spark-1.2-contributor",
            scaffold_version="2.4.6",
            time_budget_seconds=3600,
        )
    except ValueError as exc:
        assert str(exc) == "provider_call_count_mismatch"
    else:
        raise AssertionError("provider-call disagreement was accepted")
