from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from gt_harness.runtime_receipts import issue_runtime_receipts
from scripts.attest_deepswe import attest_deepswe, main

TASK = "abs-module-cache-flags"
REQUESTED = "meta/muse-spark-1.2-contributor"
EFFECTIVE = "openai/meta/muse-spark-1.2-contributor"


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _fixture(root: Path, *, source_sha: str = "f" * 40) -> tuple[Path, Path]:
    plan = {
        "schema": "gt.deepswe_gt_harness_plan.v1",
        "source_sha": source_sha,
        "benchmark_sha": "b" * 40,
        "task_ids": [TASK],
        "task_count": 1,
        "matrix": [{"task": TASK, "time_budget_seconds": 3600}],
        "task_order_sha256": hashlib.sha256((TASK + "\n").encode()).hexdigest(),
        "language_counts": {"go": 1},
        "requested_model": REQUESTED,
        "effective_model": EFFECTIVE,
        "agent": "miniswe",
        "agent_scaffold_version": "2.4.6",
        "treatment": "groundtruth",
        "provider_route_sha256": "2" * 64,
        "paid_run_approval": {"approved": True, "input": "approve_paid_run"},
        "baseline": None,
    }
    _write(root / "deepswe20-plan.json", plan)
    _write(
        root / "provider-gate.json",
        {
            "schema": "gt.provider_preflight.v1",
            "status": "PASS",
            "source_sha": source_sha,
            "mode": "live",
            "provider_ready": True,
            "paid_run_approved": True,
            "model": REQUESTED,
            "route_sha256": "2" * 64,
        },
    )
    trial = root / "tasks" / "trial"
    agent = trial / "agent"
    result_path = trial / "result.json"
    _write(result_path, {"task_name": TASK, "trial_name": "trial-1"})
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
        {
            "schema": "gt.official_verifier_result.v1",
            "benchmark_suite": "deepswe",
            "task_id": TASK,
            "status": "GRADED",
            "reward": 0,
            "product_source_sha": source_sha,
            "product_receipt_present": True,
            "runner_result_path": result_path.relative_to(root).as_posix(),
            "runner_result_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
        },
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


def test_error_product_receipt_fails_attestation_closed(tmp_path: Path) -> None:
    _adapter, product = _fixture(tmp_path)
    product_row = json.loads(product.read_text(encoding="utf-8"))
    product_row["status"] = "ERROR"
    product_row["research_valid"] = False
    product_row["receipt_issuance"] = {
        "code": "runtime_receipt_issuance_failed",
        "type": "ValueError",
        "message": "injected conservation failure",
    }
    _write(product, product_row)

    receipt = _attest(tmp_path)

    assert receipt["status"] == "FAIL"
    assert f"product_receipt:{TASK}:product_not_completed" in receipt["errors"]


def test_partial_run_preserves_graded_pass_and_explicit_error(tmp_path: Path) -> None:
    _fixture(tmp_path)
    failed_task = "failed-task"
    plan_path = tmp_path / "deepswe20-plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["task_ids"].append(failed_task)
    plan["task_count"] = 2
    plan["matrix"].append(
        {"task": failed_task, "time_budget_seconds": 3600}
    )
    plan["language_counts"] = {"go": 1, "typescript": 1}
    plan["task_order_sha256"] = hashlib.sha256(
        ("\n".join(plan["task_ids"]) + "\n").encode()
    ).hexdigest()
    _write(plan_path, plan)

    passing = next(
        (tmp_path / "tasks").rglob("agent/official-verifier-result.json")
    )
    passing_row = json.loads(passing.read_text(encoding="utf-8"))
    passing_row["reward"] = 1
    _write(passing, passing_row)

    failed_trial = tmp_path / "tasks" / "failed-wrapper" / "failed__trial"
    failed_result = failed_trial / "result.json"
    _write(
        failed_result,
        {"task_name": failed_task, "trial_name": "failed-trial"},
    )
    _write(
        failed_trial / "agent" / "official-verifier-result.json",
        {
            "schema": "gt.official_verifier_result.v1",
            "benchmark_suite": "deepswe",
            "task_id": failed_task,
            "status": "ERROR",
            "reward": None,
            "failure_class": "setup_failure",
            "error_code": "runner_setup_or_execution_failed",
            "product_source_sha": "f" * 40,
            "product_receipt_present": False,
            "runner_result_path": failed_result.relative_to(tmp_path).as_posix(),
            "runner_result_sha256": hashlib.sha256(
                failed_result.read_bytes()
            ).hexdigest(),
        },
    )

    receipt = _attest(tmp_path)

    assert receipt["status"] == "FAIL"
    assert receipt["graded"] == 1
    assert receipt["solved"] == 1
    assert receipt["outcomes"][TASK] == {
        "status": "GRADED",
        "graded": True,
        "reward": 1,
        "solved": True,
        "failure_class": "graded",
        "error_code": "",
    }
    assert receipt["outcomes"][failed_task] == {
        "status": "ERROR",
        "graded": False,
        "reward": None,
        "solved": False,
        "failure_class": "setup_failure",
        "error_code": "runner_setup_or_execution_failed",
    }


@pytest.mark.parametrize("task_job_result", ["failure", "cancelled"])
def test_non_success_task_job_fails_closed(
    tmp_path: Path, task_job_result: str
) -> None:
    _fixture(tmp_path)

    receipt = attest_deepswe(
        tmp_path,
        source_sha="f" * 40,
        task_job_result=task_job_result,
        workflow_run_id="offline",
    )

    assert receipt["status"] == "FAIL"
    assert receipt["errors"] == [
        f"task_job_result_not_success:{task_job_result}"
    ]


def test_malformed_official_verifier_writes_durable_fail_receipt(
    tmp_path: Path,
) -> None:
    _fixture(tmp_path)
    verifier = next(
        tmp_path.rglob("agent/official-verifier-result.json")
    )
    verifier.write_text("{not-json", encoding="utf-8")
    output = tmp_path / "attestation" / "deepswe20-attestation.json"

    exit_code = main(
        [
            "--root", str(tmp_path),
            "--source-sha", "f" * 40,
            "--task-job-result", "success",
            "--workflow-run-id", "offline",
            "--output", str(output),
        ]
    )

    assert exit_code == 1
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["status"] == "FAIL"
    assert receipt["outcomes"][TASK]["error_code"] == (
        "official_verifier_result_missing"
    )
    assert receipt["errors"] == [
        "invalid_official_verifier:tasks/trial/agent/official-verifier-result.json:JSONDecodeError",
        "official_verifier_task_set_mismatch",
    ]


def _run_cli(root: Path, output: Path) -> tuple[int, dict]:
    exit_code = main(
        [
            "--root", str(root),
            "--source-sha", "f" * 40,
            "--task-job-result", "success",
            "--workflow-run-id", "offline",
            "--output", str(output),
        ]
    )
    return exit_code, json.loads(output.read_text(encoding="utf-8"))


def test_invalid_numeric_receipt_field_writes_durable_fail_receipt(
    tmp_path: Path,
) -> None:
    _adapter, product = _fixture(tmp_path)
    row = json.loads(product.read_text(encoding="utf-8"))
    row["provider_calls"] = "not-an-int"
    _write(product, row)

    exit_code, receipt = _run_cli(
        tmp_path, tmp_path / "attestation" / "deepswe20-attestation.json"
    )

    assert exit_code == 1
    assert receipt["status"] == "FAIL"
    assert f"invalid_product_receipt_field:{TASK}:provider_calls" in receipt["errors"]


def test_duplicate_planned_task_writes_durable_fail_receipt(tmp_path: Path) -> None:
    _fixture(tmp_path)
    plan_path = tmp_path / "deepswe20-plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["task_ids"].append(TASK)
    plan["task_count"] = 2
    _write(plan_path, plan)

    exit_code, receipt = _run_cli(
        tmp_path, tmp_path / "attestation" / "deepswe20-attestation.json"
    )

    assert exit_code == 1
    assert receipt["status"] == "FAIL"
    assert "duplicate_planned_task" in receipt["errors"]
    assert list(receipt["outcomes"]) == [TASK]


def test_missing_plan_writes_minimal_durable_fail_receipt(tmp_path: Path) -> None:
    output = tmp_path / "attestation" / "deepswe20-attestation.json"

    exit_code, receipt = _run_cli(tmp_path, output)

    assert exit_code == 1
    assert receipt["status"] == "FAIL"
    assert receipt["schema"] == "gt.deepswe_gt_harness_attestation_error.v1"
    assert receipt["errors"] == [
        "attestation_construction_failed:required_artifact_missing"
    ]
    assert receipt["primary_error"]["evidence_ref"] == "deepswe20-plan.json"
    assert receipt["outcomes"] == {}


@pytest.mark.parametrize("value", [3.9, True])
def test_non_integer_provider_call_count_fails_closed(
    tmp_path: Path, value: object
) -> None:
    _adapter, product = _fixture(tmp_path)
    row = json.loads(product.read_text(encoding="utf-8"))
    row["provider_calls"] = value
    _write(product, row)

    receipt = _attest(tmp_path)

    assert receipt["status"] == "FAIL"
    assert f"invalid_product_receipt_field:{TASK}:provider_calls" in receipt["errors"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0])
def test_non_finite_or_negative_total_cost_fails_closed(
    tmp_path: Path, value: float
) -> None:
    _adapter, product = _fixture(tmp_path)
    row = json.loads(product.read_text(encoding="utf-8"))
    row["total_cost"] = value
    _write(product, row)

    receipt = _attest(tmp_path)

    assert receipt["status"] == "FAIL"
    assert f"invalid_product_receipt_field:{TASK}:total_cost" in receipt["errors"]
    assert receipt["product_rows"][0]["total_cost"] == 0.0


def test_string_verified_flag_fails_closed(tmp_path: Path) -> None:
    _adapter, product = _fixture(tmp_path)
    row = json.loads(product.read_text(encoding="utf-8"))
    row["treatment_receipt"]["verified"] = "false"
    _write(product, row)

    receipt = _attest(tmp_path)

    assert receipt["status"] == "FAIL"
    assert f"invalid_product_receipt_field:{TASK}:verified" in receipt["errors"]
    assert receipt["product_rows"][0]["verified"] is False


def test_boolean_official_reward_cannot_manufacture_solve(tmp_path: Path) -> None:
    _fixture(tmp_path)
    verifier = next(tmp_path.rglob("agent/official-verifier-result.json"))
    row = json.loads(verifier.read_text(encoding="utf-8"))
    row["reward"] = True
    _write(verifier, row)

    receipt = _attest(tmp_path)

    assert receipt["status"] == "FAIL"
    assert f"official_verifier_reward_invalid:{TASK}" in receipt["errors"]
    assert receipt["graded"] == 0
    assert receipt["solved"] == 0


@pytest.mark.parametrize(
    ("target", "mutate", "expected"),
    [
        (
            "plan",
            lambda row: row.update(task_order_sha256="0" * 64),
            "planned_task_order_digest_mismatch",
        ),
        (
            "plan",
            lambda row: row["paid_run_approval"].update(approved=False),
            "paid_run_approval_invalid",
        ),
        (
            "gate",
            lambda row: row.update(provider_ready=False),
            "provider_gate_live_approval_invalid",
        ),
        (
            "gate",
            lambda row: row.update(route_sha256="0" * 64),
            "provider_gate_route_mismatch",
        ),
    ],
)
def test_plan_and_provider_gate_mutations_fail_closed(
    tmp_path: Path, target: str, mutate, expected: str
) -> None:
    _fixture(tmp_path)
    path = tmp_path / (
        "deepswe20-plan.json" if target == "plan" else "provider-gate.json"
    )
    row = json.loads(path.read_text(encoding="utf-8"))
    mutate(row)
    _write(path, row)

    receipt = _attest(tmp_path)

    assert receipt["status"] == "FAIL"
    assert expected in receipt["errors"]


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("schema", "wrong", "official_verifier_schema_mismatch"),
        ("runner_result_sha256", "0" * 64, "official_verifier_result_digest_mismatch"),
    ],
)
def test_official_verifier_provenance_mutations_cannot_grade(
    tmp_path: Path, field: str, value: object, expected: str
) -> None:
    _fixture(tmp_path)
    verifier = next(tmp_path.rglob("agent/official-verifier-result.json"))
    row = json.loads(verifier.read_text(encoding="utf-8"))
    row[field] = value
    _write(verifier, row)

    receipt = _attest(tmp_path)

    assert receipt["status"] == "FAIL"
    assert f"{expected}:{TASK}" in receipt["errors"]
    assert receipt["graded"] == 0


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
