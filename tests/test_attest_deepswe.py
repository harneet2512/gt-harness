from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

import scripts.attest_deepswe as attest_module
from gt_engine.attribution import DIRECT_FEATURES
from gt_engine.feature_matrix import digest_body
from gt_harness.runtime_receipts import issue_runtime_receipts
from scripts.attest_deepswe import _total_cost, attest_deepswe, main
from scripts.gt_audit import artifact_corpus_sha256, audit_digest_sha256
from scripts.provider_preflight import load_route

TASK = "abs-module-cache-flags"
REQUESTED = "meta/muse-spark-1.2-contributor"
EFFECTIVE = "openai/meta/muse-spark-1.2-contributor"


@pytest.fixture(autouse=True)
def _single_task_canonical_cohort(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(attest_module, "CANONICAL_TASK_IDS", (TASK,))


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _fixture(root: Path, *, source_sha: str = "f" * 40) -> tuple[Path, Path]:
    repo_root = Path(__file__).resolve().parents[1]
    bundle = json.loads(
        (repo_root / "config" / "deepswe_product_bundle_v1.json").read_text()
    )
    bundle_task = next(row for row in bundle["tasks"] if row["task_id"] == TASK)
    route, route_digest = load_route(repo_root / "config" / "provider_route.v1.json")
    plan = {
        "schema": "gt.deepswe_gt_harness_plan.v1",
        "source_sha": source_sha,
        "benchmark_sha": bundle["dataset"]["commit"],
        "task_ids": [TASK],
        "task_count": 1,
        "task_config_identity": bundle["dataset"]["task_config_identity"],
        "matrix": [{
            "ordinal": 1,
            "task": TASK,
            "language": bundle_task["language"],
            "outer_agent_timeout_seconds": 3630,
            "time_budget_seconds": 3600,
            "task_config_sha256": bundle_task["task_config_sha256"],
            "container_image": bundle_task["container_image"],
            "container_digest": bundle_task["container_digest"],
        }],
        "task_order_sha256": hashlib.sha256((TASK + "\n").encode()).hexdigest(),
        "language_counts": {"go": 1},
        "requested_model": REQUESTED,
        "effective_model": EFFECTIVE,
        "attempts_per_task": 1,
        "max_parallel": 1,
        "agent": "eval.pier_gt_harness_adapter:PierGtHarnessMiniSwe246Agent",
        "agent_scaffold": "mini-swe-agent",
        "agent_scaffold_version": "2.4.6",
        "treatment": "groundtruth",
        "provider_route_id": route["route_id"],
        "provider_route_sha256": route_digest,
        "provider": route["provider"],
        "provider_base_url": route["base_url"],
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
            "route_id": route["route_id"],
            "provider": route["provider"],
            "base_url": route["base_url"],
            "model": REQUESTED,
            "route_sha256": route_digest,
            "error_code": None,
            "checks": {
                "credential_valid": True,
                "key_limit_available": True,
                "model_visible": True,
                "model_canary_served": True,
            },
            "account_amounts_recorded": False,
            "provider_inference_attempts": 1,
            "provider_inference_calls": 1,
        },
    )
    job = root / "tasks" / "job"
    trial = job / "trial"
    agent = trial / "agent"
    result_path = trial / "result.json"
    _write(
        result_path,
        {
            "task_name": TASK,
            "trial_name": "trial-1",
            "verifier_result": {"rewards": {"reward": 0}},
        },
    )
    aggregate_path = job / "result.json"
    _write(
        aggregate_path,
        {
            "n_total_trials": 1,
            "stats": {"evals": {"task": {"metrics": [{"reward": 0}]}}},
        },
    )
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
                "verified": True,
                "unmet_predicates": [],
                "unverified_predicates": [],
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
            "solved": False,
            "failure_class": "graded",
            "error_code": "",
            "product_source_sha": source_sha,
            "product_receipt_present": True,
            "runner_result_path": aggregate_path.relative_to(root).as_posix(),
            "runner_result_sha256": hashlib.sha256(aggregate_path.read_bytes()).hexdigest(),
        },
    )
    audit = {
        "schema": "gt.audit.v1",
        "source_sha": source_sha,
        "workflow_run_id": "offline",
        "run_dir": "attestation/tasks",
        "artifact_corpus_sha256": artifact_corpus_sha256(root / "tasks"),
        "tasks": [{"task_name": TASK, "verdict": "GREEN"}],
    }
    audit["audit_digest_sha256"] = audit_digest_sha256(audit)
    audit_path = root / "gt-audit.json"
    _write(audit_path, audit)
    live_gate = {
        "schema": "gt.live_acceptance.v1",
        "passed": True,
        "task_count": 1,
        "expected_tasks": 1,
        "expected_model": REQUESTED,
        "observed_models": [REQUESTED],
        "issues": [],
        "source_sha": source_sha,
        "workflow_run_id": "offline",
        "audit_digest_sha256": audit["audit_digest_sha256"],
        "audit_file_sha256": hashlib.sha256(audit_path.read_bytes()).hexdigest(),
    }
    live_gate["report_digest_sha256"] = digest_body(
        live_gate, field="report_digest_sha256"
    )
    _write(root / "gt-live-gate.json", live_gate)
    feature_rows = []
    for identity, spec in sorted(DIRECT_FEATURES.items()):
        feature = {
            "identity": identity,
            "kind": spec["kind"],
            "disposition": "WITNESSED",
            "trigger_source": "tests/provider_free.py",
            "evidence": {"exit_code": 0},
            "freshness_pins": {"source_revision": source_sha},
            "receipt_digest_sha256": None,
        }
        feature["cell_digest_sha256"] = digest_body(
            feature, field="cell_digest_sha256"
        )
        feature_rows.append(feature)
    feature_matrix = {
        "schema": "gt.feature_matrix.v1",
        "source_revision": source_sha,
        "generated_at": "2026-09-02T00:00:00Z",
        "identity_count": len(feature_rows),
        "rows": feature_rows,
    }
    feature_matrix["matrix_digest_sha256"] = digest_body(
        feature_matrix, field="matrix_digest_sha256"
    )
    _write(root / "feature-matrix.json", feature_matrix)
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
        f"official_verifier_product_receipt_mismatch:{TASK}",
        "official_verifier_task_set_mismatch",
        "canonical_audit_failed_or_incomplete",
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
        "canonical_audit_failed_or_incomplete",
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


def test_partial_run_preserves_graded_pass_and_explicit_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fixture(tmp_path)
    failed_task = "adaptix-name-mapping-aliases"
    repo_root = Path(__file__).resolve().parents[1]
    bundle = json.loads(
        (repo_root / "config" / "deepswe_product_bundle_v1.json").read_text()
    )
    failed_bundle = next(
        row for row in bundle["tasks"] if row["task_id"] == failed_task
    )
    monkeypatch.setattr(attest_module, "CANONICAL_TASK_IDS", (TASK, failed_task))
    plan_path = tmp_path / "deepswe20-plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["task_ids"].append(failed_task)
    plan["task_count"] = 2
    plan["max_parallel"] = 2
    plan["matrix"].append(
        {
            "ordinal": 2,
            "task": failed_task,
            "language": failed_bundle["language"],
            "outer_agent_timeout_seconds": 3630,
            "time_budget_seconds": 3600,
            "task_config_sha256": failed_bundle["task_config_sha256"],
            "container_image": failed_bundle["container_image"],
            "container_digest": failed_bundle["container_digest"],
        }
    )
    plan["language_counts"] = {"go": 1, "python": 1}
    plan["task_order_sha256"] = hashlib.sha256(
        ("\n".join(plan["task_ids"]) + "\n").encode()
    ).hexdigest()
    _write(plan_path, plan)

    passing = next(
        (tmp_path / "tasks").rglob("agent/official-verifier-result.json")
    )
    passing_row = json.loads(passing.read_text(encoding="utf-8"))
    passing_row["reward"] = 1
    passing_row["solved"] = True
    _write(passing, passing_row)
    passing_result = next(
        path for path in (tmp_path / "tasks").rglob("result.json")
        if path.parent.name == "trial"
    )
    passing_result_row = json.loads(passing_result.read_text(encoding="utf-8"))
    passing_result_row["verifier_result"]["rewards"]["reward"] = 1
    _write(passing_result, passing_result_row)
    passing_aggregate = passing_result.parent.parent / "result.json"
    passing_aggregate_row = json.loads(
        passing_aggregate.read_text(encoding="utf-8")
    )
    passing_aggregate_row["stats"]["evals"]["task"]["metrics"][0]["reward"] = 1
    _write(passing_aggregate, passing_aggregate_row)
    passing_row["runner_result_sha256"] = hashlib.sha256(
        passing_aggregate.read_bytes()
    ).hexdigest()
    _write(passing, passing_row)

    failed_trial = tmp_path / "tasks" / "failed-wrapper" / "failed__trial"
    failed_aggregate = failed_trial.parent / "result.json"
    _write(failed_aggregate, {"n_total_trials": 1, "stats": {"evals": {}}})
    failed_result = failed_trial / "result.json"
    _write(
        failed_result,
        {
            "task_name": failed_task,
            "trial_name": "failed-trial",
            "exception_info": {"exception_type": "NonZeroAgentExitCodeError"},
        },
    )
    _write(
        failed_trial / "agent" / "official-verifier-result.json",
        {
            "schema": "gt.official_verifier_result.v1",
            "benchmark_suite": "deepswe",
            "task_id": failed_task,
            "status": "ERROR",
            "reward": None,
            "solved": None,
            "failure_class": "setup_failure",
            "error_code": "runner_setup_or_execution_failed",
            "product_source_sha": "f" * 40,
            "product_receipt_present": False,
            "runner_result_path": failed_aggregate.relative_to(tmp_path).as_posix(),
            "runner_result_sha256": hashlib.sha256(
                failed_aggregate.read_bytes()
            ).hexdigest(),
        },
    )
    audit_path = tmp_path / "gt-audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["tasks"].append({"task_name": failed_task, "verdict": "RED"})
    audit["artifact_corpus_sha256"] = artifact_corpus_sha256(tmp_path / "tasks")
    audit["audit_digest_sha256"] = audit_digest_sha256(audit)
    _write(audit_path, audit)
    live_path = tmp_path / "gt-live-gate.json"
    live = json.loads(live_path.read_text(encoding="utf-8"))
    live.update(passed=False, task_count=2, expected_tasks=2)
    live["issues"] = ["failed task"]
    live["audit_digest_sha256"] = audit["audit_digest_sha256"]
    live["audit_file_sha256"] = hashlib.sha256(audit_path.read_bytes()).hexdigest()
    live["report_digest_sha256"] = digest_body(
        live, field="report_digest_sha256"
    )
    _write(live_path, live)

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
        "invalid_official_verifier:tasks/job/trial/agent/official-verifier-result.json:JSONDecodeError",
        "official_verifier_task_set_mismatch",
        "canonical_audit_failed_or_incomplete",
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
    assert list(receipt["outcomes"]) == [TASK]
    assert receipt["outcomes"][TASK]["error_code"] == "official_verifier_result_missing"


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


def test_numeric_official_reward_cannot_manufacture_solve(tmp_path: Path) -> None:
    _fixture(tmp_path)
    verifier = next(tmp_path.rglob("agent/official-verifier-result.json"))
    row = json.loads(verifier.read_text(encoding="utf-8"))
    row.update(reward=1, solved=True)
    _write(verifier, row)

    receipt = _attest(tmp_path)

    assert receipt["status"] == "FAIL"
    assert f"official_verifier_recomputation_mismatch:{TASK}" in receipt["errors"]
    assert receipt["graded"] == 0
    assert receipt["solved"] == 0


def test_coordinated_aggregate_and_verifier_reward_mutation_cannot_solve(
    tmp_path: Path,
) -> None:
    _fixture(tmp_path)
    verifier = next(tmp_path.rglob("agent/official-verifier-result.json"))
    official = json.loads(verifier.read_text(encoding="utf-8"))
    aggregate = tmp_path / official["runner_result_path"]
    aggregate_row = json.loads(aggregate.read_text(encoding="utf-8"))
    aggregate_row["stats"]["evals"]["task"]["metrics"][0]["reward"] = 1
    _write(aggregate, aggregate_row)
    official.update(
        reward=1,
        solved=True,
        runner_result_sha256=hashlib.sha256(aggregate.read_bytes()).hexdigest(),
    )
    _write(verifier, official)

    receipt = _attest(tmp_path)

    assert receipt["status"] == "FAIL"
    assert f"official_verifier_recomputation_mismatch:{TASK}" in receipt["errors"]
    assert receipt["solved"] == 0


def test_official_verifier_cannot_claim_arbitrary_in_root_file(tmp_path: Path) -> None:
    _fixture(tmp_path)
    verifier = next(tmp_path.rglob("agent/official-verifier-result.json"))
    row = json.loads(verifier.read_text(encoding="utf-8"))
    arbitrary = tmp_path / "deepswe20-plan.json"
    row["runner_result_path"] = arbitrary.relative_to(tmp_path).as_posix()
    row["runner_result_sha256"] = hashlib.sha256(arbitrary.read_bytes()).hexdigest()
    _write(verifier, row)

    receipt = _attest(tmp_path)

    assert receipt["status"] == "FAIL"
    assert f"official_verifier_result_not_canonical:{TASK}" in receipt["errors"]
    assert receipt["graded"] == 0


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
        (
            "gate",
            lambda row: row["checks"].update(model_canary_served=False),
            "provider_gate_checks_invalid",
        ),
        (
            "plan",
            lambda row: row.update(task_count=True),
            "planned_task_count_mismatch",
        ),
        (
            "plan",
            lambda row: row["matrix"][0].update(container_digest="sha256:" + "0" * 64),
            "planned_task_provenance_mismatch",
        ),
        (
            "plan",
            lambda row: row.update(provider_route_sha256="0" * 64),
            "planned_provider_route_mismatch",
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
    assert any(expected in error for error in receipt["errors"])


def test_coordinated_plan_and_gate_route_mutation_fails_closed(
    tmp_path: Path,
) -> None:
    _fixture(tmp_path)
    plan_path = tmp_path / "deepswe20-plan.json"
    gate_path = tmp_path / "provider-gate.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    for row in (plan, gate):
        row["provider_route_id"] = "mutated-route"
        row["provider_route_sha256"] = "0" * 64
        row["route_id"] = "mutated-route"
        row["route_sha256"] = "0" * 64
    gate["checks"] = {key: False for key in gate["checks"]}
    gate["provider_inference_attempts"] = 0
    gate["provider_inference_calls"] = 0
    _write(plan_path, plan)
    _write(gate_path, gate)

    receipt = _attest(tmp_path)

    assert receipt["status"] == "FAIL"
    assert "planned_provider_route_mismatch" in receipt["errors"]
    assert "provider_gate_route_mismatch" in receipt["errors"]
    assert "provider_gate_checks_invalid" in receipt["errors"]


def test_unknown_provider_gate_field_fails_without_leaking_value(
    tmp_path: Path,
) -> None:
    _fixture(tmp_path)
    path = tmp_path / "provider-gate.json"
    gate = json.loads(path.read_text(encoding="utf-8"))
    gate["api_key"] = "SECRET_CANARY"
    _write(path, gate)

    receipt = _attest(tmp_path)

    assert receipt["status"] == "FAIL"
    assert "provider_gate_fields_invalid" in receipt["errors"]
    assert "SECRET_CANARY" not in json.dumps(receipt)


def test_unknown_nested_provider_check_fails_without_leaking_value(
    tmp_path: Path,
) -> None:
    _fixture(tmp_path)
    path = tmp_path / "provider-gate.json"
    gate = json.loads(path.read_text(encoding="utf-8"))
    gate["checks"]["api_key"] = "SECRET_CANARY_NESTED"
    _write(path, gate)

    receipt = _attest(tmp_path)

    assert receipt["status"] == "FAIL"
    assert "provider_gate_checks_invalid" in receipt["errors"]
    assert "SECRET_CANARY_NESTED" not in json.dumps(receipt)


@pytest.mark.parametrize(
    ("artifact", "mutate", "expected"),
    [
        (
            "gt-audit.json",
            lambda row: row["tasks"][0].update(verdict="RED"),
            "canonical_audit_failed_or_incomplete",
        ),
        (
            "gt-live-gate.json",
            lambda row: row.update(passed=False),
            "canonical_live_gate_failed_or_incomplete",
        ),
        (
            "feature-matrix.json",
            lambda row: row.update(source_revision="0" * 40),
            "canonical_feature_matrix:source_revision does not match checkout HEAD",
        ),
    ],
)
def test_canonical_acceptance_evidence_mutations_fail_closed(
    tmp_path: Path, artifact: str, mutate, expected: str
) -> None:
    _fixture(tmp_path)
    path = tmp_path / artifact
    row = json.loads(path.read_text(encoding="utf-8"))
    mutate(row)
    if artifact == "feature-matrix.json":
        row["matrix_digest_sha256"] = digest_body(
            row, field="matrix_digest_sha256"
        )
    _write(path, row)

    receipt = _attest(tmp_path)

    assert receipt["status"] == "FAIL"
    assert expected in receipt["errors"]


def test_missing_canonical_audit_preserves_per_task_outcome(tmp_path: Path) -> None:
    _fixture(tmp_path)
    (tmp_path / "gt-audit.json").unlink()

    receipt = _attest(tmp_path)

    assert receipt["status"] == "FAIL"
    assert receipt["errors"] == [
        "canonical_audit_missing",
        "canonical_live_gate_failed_or_incomplete",
    ]
    assert receipt["outcomes"][TASK]["graded"] is True


def test_recomputed_synthetic_audit_still_fails_corpus_binding(tmp_path: Path) -> None:
    _fixture(tmp_path)
    path = tmp_path / "gt-audit.json"
    audit = json.loads(path.read_text(encoding="utf-8"))
    audit["run_dir"] = "stale-other-run"
    audit["artifact_corpus_sha256"] = "0" * 64
    audit["audit_digest_sha256"] = audit_digest_sha256(audit)
    _write(path, audit)

    receipt = _attest(tmp_path)

    assert receipt["status"] == "FAIL"
    assert "canonical_audit_failed_or_incomplete" in receipt["errors"]


def test_unverified_completion_and_unmet_predicates_fail_closed(
    tmp_path: Path,
) -> None:
    _adapter, product = _fixture(tmp_path)
    row = json.loads(product.read_text(encoding="utf-8"))
    row["treatment_receipt"]["verified"] = False
    row["treatment_receipt"]["unmet_predicates"] = ["prefix_semantics"]
    _write(product, row)

    receipt = _attest(tmp_path)

    assert f"product_completion_unverified:{TASK}" in receipt["errors"]
    assert f"product_unmet_predicates:{TASK}" in receipt["errors"]


def test_canonical_twenty_task_cohort_cannot_be_truncated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fixture(tmp_path)
    monkeypatch.setattr(attest_module, "CANONICAL_TASK_IDS", (TASK, "missing-task"))

    receipt = _attest(tmp_path)

    assert "planned_canonical_cohort_mismatch" in receipt["errors"]


def test_total_cost_overflow_is_durable_and_finite() -> None:
    errors: list[str] = []

    total = _total_cost(
        [{"total_cost": 1e308}, {"total_cost": 1e308}], errors
    )

    assert total == 0.0
    assert errors == ["product_total_cost_overflow"]


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
