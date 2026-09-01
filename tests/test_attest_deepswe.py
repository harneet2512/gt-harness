from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from gt_harness.runtime_receipts import issue_runtime_receipts
from scripts.attest_deepswe import attest_deepswe

TASK = "abs-module-cache-flags"
REQUESTED = "meta/muse-spark-1.2-contributor"
EFFECTIVE = "openai/meta/muse-spark-1.2-contributor"


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _fixture(root: Path, *, source_sha: str = "f" * 40) -> tuple[Path, Path]:
    plan = {
        "source_sha": source_sha,
        "benchmark_sha": "b" * 40,
        "task_ids": [TASK],
        "task_count": 1,
        "matrix": [{"task": TASK, "time_budget_seconds": 3600}],
        "task_order_sha256": "1" * 64,
        "language_counts": {"go": 1},
        "requested_model": REQUESTED,
        "effective_model": EFFECTIVE,
        "agent": "miniswe",
        "agent_scaffold_version": "2.4.6",
        "treatment": "groundtruth",
        "paid_run_approval": True,
        "baseline": None,
    }
    _write(root / "deepswe20-plan.json", plan)
    _write(root / "provider-gate.json", {"status": "PASS", "source_sha": source_sha})
    trial = root / "tasks" / "trial"
    agent = trial / "agent"
    _write(trial / "result.json", {"task_name": TASK, "trial_name": "trial-1"})
    trajectory = agent / "miniswe_trajectory.json"
    report = agent / "miniswe_report.json"
    _write(
        trajectory,
        {"messages": [], "info": {"model_stats": {"api_calls": 3}, "exit_status": "Submitted"}},
    )
    _write(
        report,
        {
            "model": REQUESTED,
            "terminal": "submitted_unverified",
            "exit_code": 0,
            "gt_mode": "advisory",
            "research_valid": True,
            "gt": {
                "terminal_requests": 3,
                "contract_shipped": True,
                "delivered_evidence": 1,
                "provider_reported_model": REQUESTED,
                "resolved_model": EFFECTIVE,
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            },
        },
    )
    state = agent / "gt-state" / "task-state"
    events = [
        {
            "event": "provider_response", "event_hash": "1" * 64,
            "usage": {"prompt_tokens": 6, "completion_tokens": 1},
        },
        {
            "event": "provider_response", "event_hash": "2" * 64,
            "usage": {"prompt_tokens": 4, "completion_tokens": 1},
        },
        {
            "event": "evidence_delivery", "event_hash": "3" * 64, "sequence": 3,
            "dedup_key": "localization", "evidence_type": "localization",
            "iteration": 0, "action_index": 0, "rendered_bytes": 100,
        },
        {
            "event": "receipt", "event_hash": "4" * 64, "sequence": 4,
            "transition": "delivered", "dedup_key": "localization",
            "evidence_type": "localization", "iteration": 0, "payload_hash": "5" * 64,
        },
        {
            "event": "provider_delivery", "event_hash": "6" * 64, "sequence": 5,
            "iteration": 1, "request_id": "request-1",
        },
        {
            "event": "dense_index_ready", "event_hash": "7" * 64, "sequence": 6,
            "query_ready": True, "model_sha256": "8" * 64,
            "tokenizer_sha256": "9" * 64, "dimension": 768,
            "document_count": 2, "query_result_count": 2, "index_sha256": "a" * 64,
        },
        {"event": "session_closed", "event_hash": "b" * 64, "sequence": 7},
    ]
    state.mkdir(parents=True, exist_ok=True)
    (state / "events.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in events), encoding="utf-8"
    )
    _write(
        state / "reproducibility_manifest.json",
        {
            "research_valid": True,
            "provider_receipts": {"request_count": 3, "valid": True},
            "event_journal": {
                "event_count": 7, "event_head": "b" * 64, "valid": True, "issues": [],
            },
        },
    )
    _write(
        agent / "gt-state" / "graph" / "graph.manifest.json",
        {
            "schema": "gt.graph_certification.v1", "binary_certified": True,
            "sqlite_quick_check": "ok", "graph_sha256": "c" * 64,
        },
    )
    issue_runtime_receipts(
        report_path=report,
        trajectory_path=trajectory,
        state_dir=agent / "gt-state",
        product_receipt_path=agent / "gt-run.json",
        adapter_receipt_path=agent / "benchmark-adapter.json",
        task_id=TASK,
        product_source_sha=source_sha,
        treatment="groundtruth",
        requested_model=REQUESTED,
        scaffold_version="2.4.6",
        time_budget_seconds=3600,
    )
    _write(
        agent / "official-verifier-result.json",
        {"task_id": TASK, "status": "GRADED", "reward": 0, "product_source_sha": source_sha},
    )
    return agent / "benchmark-adapter.json", agent / "gt-run.json"


def _attest(root: Path, source_sha: str = "f" * 40) -> dict:
    return attest_deepswe(
        root, source_sha=source_sha, task_job_result="success", workflow_run_id="offline"
    )


def test_positive_and_provider_failure_attempt_conservation(tmp_path: Path) -> None:
    _fixture(tmp_path)
    receipt = _attest(tmp_path)
    assert receipt["status"] == "PASS"
    assert receipt["errors"] == []
    assert receipt["product_totals"]["provider_calls"] == 3
    assert receipt["product_totals"]["provider_completed_calls"] == 2
    assert receipt["product_totals"]["provider_failed_calls"] == 1


def test_historical_missing_receipts_are_exact(tmp_path: Path) -> None:
    adapter, product = _fixture(tmp_path)
    adapter.unlink()
    product.unlink()
    assert _attest(tmp_path)["errors"] == [
        "adapter_receipt_task_set_mismatch",
        "product_receipt_task_set_mismatch",
    ]


def test_historical_model_route_mismatch_is_exact(tmp_path: Path) -> None:
    adapter, product = _fixture(tmp_path)
    adapter_row = json.loads(adapter.read_text(encoding="utf-8"))
    adapter_row["effective_model"] = REQUESTED
    _write(adapter, adapter_row)
    product_row = json.loads(product.read_text(encoding="utf-8"))
    product_row["effective_model"] = REQUESTED
    product_row["treatment_receipt"]["provider_identity"]["resolved"] = REQUESTED
    report_path = product.with_name("miniswe_report.json")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["gt"]["resolved_model"] = REQUESTED
    _write(report_path, report)
    product_row["integrity"]["report_sha256"] = hashlib.sha256(
        report_path.read_bytes()
    ).hexdigest()
    _write(product, product_row)
    assert _attest(tmp_path)["errors"] == [
        f"effective_model_mismatch:{TASK}",
        f"product_effective_model_mismatch:{TASK}",
    ]


@pytest.mark.parametrize(
    ("target", "mutation", "expected"),
    [
        ("adapter", lambda row: row.update(product_command="other"), "adapter_contract_mismatch"),
        ("adapter", lambda row: row.update(attempt=2), "adapter_contract_mismatch"),
        ("adapter", lambda row: row.update(treatment="bare"), "adapter_treatment_mismatch"),
        ("adapter", lambda row: row.update(requested_model="other"), "requested_model_mismatch"),
        ("adapter", lambda row: row.update(agent_scaffold_version="2.3.0"), "scaffold_version_mismatch"),
        ("adapter", lambda row: row.update(product_source_sha="0" * 40), "adapter_source_sha_mismatch"),
        ("adapter", lambda row: row.update(time_budget_seconds=1), "adapter_time_budget_mismatch"),
        ("product", lambda row: row.update(provider_calls=4), "product_provider_calls_mismatch"),
        ("product", lambda row: row.update(input_tokens=11), "product_input_token_conservation_failed"),
        ("product", lambda row: row["integrity"].update(trajectory_sha256="0" * 64), "product_trajectory_digest_mismatch"),
        ("product", lambda row: row["treatment_receipt"].update(delivery_count=5), "treatment_delivery_count_mismatch"),
        ("product", lambda row: row["treatment_receipt"]["provider_delivery_receipts"][0].update(same_observation=False), "treatment_delivery_late"),
        ("product", lambda row: row["treatment_receipt"]["provider_delivery_receipts"][0].update(context_byte_count=2001), "treatment_delivery_context_budget_exceeded"),
        ("product", lambda row: row["treatment_receipt"]["dense_index_receipt"].update(query_ready=False), "treatment_dense_index_not_ready"),
    ],
)
def test_attestation_mutation_matrix(
    tmp_path: Path, target: str, mutation, expected: str
) -> None:
    adapter, product = _fixture(tmp_path)
    path = adapter if target == "adapter" else product
    row = deepcopy(json.loads(path.read_text(encoding="utf-8")))
    mutation(row)
    _write(path, row)
    assert any(expected in error for error in _attest(tmp_path)["errors"])
