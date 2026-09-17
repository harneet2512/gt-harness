from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from gt_engine.attribution import DIRECT_FEATURES
from gt_engine.feature_matrix import digest_body
from gt_harness.runtime_receipts import issue_runtime_receipts
from scripts.attest_deepswe import _task_name, attest_deepswe
from scripts.benchmark_suites import (
    GATE_STAGE,
    load_suite,
    select_stage_tasks,
    stage_timeout_cap_seconds,
    validate_stage_inputs,
)
from scripts.gt_audit import artifact_corpus_sha256, audit_digest_sha256
from scripts.provider_preflight import load_route
from tests.conftest import write_certifiable_graph

SWELIVE_TASK = "cyclotruc__gitingest-94"
SWELIVE_OTHER = "dynaconf__dynaconf-1241"
REQUESTED = "deepseek/deepseek-v4-flash-0731"
EFFECTIVE = "openai/deepseek/deepseek-v4-flash-0731"


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_swelive_suite_binds_the_in_repo_cohort() -> None:
    suite = load_suite("swelive")
    manifest_path = (
        Path(__file__).resolve().parents[1] / "swelive-bench" / "manifest.json"
    )
    assert suite.suite_id == "swelive"
    assert suite.canonical_task_ids == (SWELIVE_TASK, SWELIVE_OTHER)
    assert suite.gate_task_id == SWELIVE_TASK
    assert suite.remainder_stage == "remaining"
    assert suite.all_stage == "all"
    assert suite.benchmark_sha == hashlib.sha256(
        manifest_path.read_bytes()
    ).hexdigest()
    assert suite.task_config_identity == "sha256_canonical_lf_v1"
    assert set(suite.trusted_tasks) == set(suite.canonical_task_ids)
    assert suite.plan_filename == "swelive-plan.json"
    assert suite.attestation_filename == "swelive-attestation.json"


def test_swelive_stage_selection_partitions_the_cohort() -> None:
    suite = load_suite("swelive")
    tasks = list(suite.canonical_task_ids)
    assert select_stage_tasks(suite, tasks, GATE_STAGE) == [SWELIVE_TASK]
    assert select_stage_tasks(suite, tasks, "remaining") == [SWELIVE_OTHER]
    assert select_stage_tasks(suite, tasks, "all") == tasks
    assert select_stage_tasks(suite, tasks, f"single:{SWELIVE_OTHER}") == [
        SWELIVE_OTHER
    ]
    with pytest.raises(ValueError):
        select_stage_tasks(suite, tasks, "single:not-a-task")
    with pytest.raises(ValueError):
        select_stage_tasks(suite, tasks[:1], "all")


def test_swelive_stage_input_rules_match_the_gate_contract() -> None:
    suite = load_suite("swelive")
    validate_stage_inputs(suite, "gate-one", "")
    validate_stage_inputs(suite, "all", "")
    validate_stage_inputs(suite, "remaining", "34766499875")
    with pytest.raises(ValueError):
        validate_stage_inputs(suite, "remaining", "")
    with pytest.raises(ValueError):
        validate_stage_inputs(suite, "gate-one", "123")
    assert stage_timeout_cap_seconds(suite, "gate-one") == 1800.0
    assert stage_timeout_cap_seconds(suite, "all") is None


def test_task_name_resolves_suite_ids_containing_double_underscore() -> None:
    canonical = {SWELIVE_TASK, SWELIVE_OTHER}
    assert _task_name("swelive/cyclotruc__gitingest-94", canonical) == SWELIVE_TASK
    assert _task_name(SWELIVE_TASK, canonical) == SWELIVE_TASK
    # A pier trial dir suffix is stripped only when the base is canonical.
    assert (
        _task_name("cyclotruc__gitingest-94__aB3x9Yz", canonical) == SWELIVE_TASK
    )
    # Unknown ids survive unmangled so the caller sees the real identity.
    assert (
        _task_name("swelive/unknown__task", canonical) == "unknown__task"
    )


def _swelive_fixture(root: Path, source_sha: str = "f" * 40) -> Path:
    suite = load_suite("swelive")
    trusted = suite.trusted_tasks[SWELIVE_TASK]
    route, route_digest = load_route(
        Path(__file__).resolve().parents[1] / "config" / "provider_route.v1.json"
    )
    plan = {
        "schema": suite.plan_schema,
        "source_sha": source_sha,
        "benchmark_sha": suite.benchmark_sha,
        "task_config_identity": suite.task_config_identity,
        "task_ids": [SWELIVE_TASK],
        "task_count": 1,
        "task_order_sha256": hashlib.sha256(
            (SWELIVE_TASK + "\n").encode()
        ).hexdigest(),
        "cohort_stage": "gate-one",
        "gate_task_id": suite.gate_task_id,
        "full_task_count": 2,
        "full_task_order_sha256": hashlib.sha256(
            ("\n".join(suite.canonical_task_ids) + "\n").encode()
        ).hexdigest(),
        "full_language_counts": {"python": 2},
        "prior_gate": None,
        "language_counts": {"python": 1},
        "attempts_per_task": 1,
        "max_parallel": 1,
        "agent": "eval.pier_gt_harness_adapter:PierGtHarnessMiniSwe246Agent",
        "agent_scaffold": "mini-swe-agent",
        "agent_scaffold_version": "2.4.6",
        "treatment": "groundtruth",
        "provider_route_id": route["route_id"],
        "provider_route_sha256": route_digest,
        "requested_model": REQUESTED,
        "effective_model": EFFECTIVE,
        "provider": route["provider"],
        "provider_base_url": route["base_url"],
        "provider_routing": route["provider_routing"],
        "cohort_pacing": dict(route["retry_pacing"]),
        "paid_run_approval": {"approved": True, "input": "approve_paid_run"},
        "baseline": {
            "schema": "gt.baseline_ref.v1",
            "kind": "none",
            "dataset": "SWE-bench-Live/SWE-bench-Live",
            "split": "lite",
        },
        "matrix": [
            {
                "ordinal": 1,
                "task": SWELIVE_TASK,
                "language": trusted["language"],
                "outer_agent_timeout_seconds": 3300,
                "agent_timeout_multiplier": 1.8333333333333333,
                "benchmark_budget_seconds": 1800,
                "gt_overhead_extension_seconds": 1500,
                "time_budget_seconds": 3060,
                "task_config_sha256": trusted["task_config_sha256"],
                "container_image": trusted["container_image"],
                "container_digest": trusted["container_digest"],
            }
        ],
    }
    _write(root / "swelive-plan.json", plan)
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
            "provider_routing": route["provider_routing"],
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
            "context_window_tokens": 131072,
            "reserved_output_tokens": route["requested_output_tokens"],
            "context_window_source": "openrouter:/models",
        },
    )
    job = root / "tasks" / "job"
    trial = job / f"{SWELIVE_TASK}__aB3x9Yz"
    agent = trial / "agent"
    result_path = trial / "result.json"
    _write(
        result_path,
        {
            "task_name": f"swelive/{SWELIVE_TASK}",
            "trial_name": f"{SWELIVE_TASK}__aB3x9Yz",
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
        {"messages": [], "info": {"model_stats": {"api_calls": 1}, "exit_status": "Submitted"}},
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
                "terminal_requests": 1,
                "contract_shipped": True,
                "delivered_evidence": 1,
                "provider_reported_model": REQUESTED,
                "resolved_model": EFFECTIVE,
                "usage": {"prompt_tokens": 6, "completion_tokens": 1},
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
            "event": "provider_admission", "event_hash": "c" * 64,
            "status": "admitted", "reason": "within_provider_window",
            "request_tokens": 100, "request_bytes": 400,
            "context_window_tokens": 131072, "reserved_output_tokens": 16384,
            "input_budget_tokens": 114688, "metadata_source": "openrouter:/models",
        },
        {
            "event": "evidence_delivery", "event_hash": "3" * 64, "sequence": 3,
            "dedup_key": "localization", "evidence_type": "localization",
            "iteration": 0, "action_index": 0, "rendered_bytes": 100,
            "delivery_identity": "5" * 64,
        },
        {
            "event": "receipt", "event_hash": "4" * 64, "sequence": 4,
            "transition": "delivered", "dedup_key": "localization",
            "evidence_type": "localization", "iteration": 0, "payload_hash": "5" * 64,
        },
        {
            "event": "provider_delivery", "event_hash": "6" * 64, "sequence": 5,
            "iteration": 1, "request_id": "request-1",
            "delivery_ids": ["5" * 64],
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
            "gt_mode": "advisory",
            "engine_integrity": {"schema": "gt.engine_integrity.v1", "valid": True,
                                 "mode": "advisory", "issues": [], "disabled_stage": ""},
            "provider_receipts": {"request_count": 1, "valid": True},
            "event_journal": {
                "event_count": 7, "event_head": "b" * 64, "valid": True, "issues": [],
            },
        },
    )
    write_certifiable_graph(
        agent / "gt-state" / "graph",
        task_id=SWELIVE_TASK,
        product_source_sha=source_sha,
    )
    issue_runtime_receipts(
        report_path=report,
        trajectory_path=trajectory,
        state_dir=agent / "gt-state",
        product_receipt_path=agent / "gt-run.json",
        adapter_receipt_path=agent / "benchmark-adapter.json",
        task_id=SWELIVE_TASK,
        product_source_sha=source_sha,
        treatment="groundtruth",
        requested_model=REQUESTED,
        scaffold_version="2.4.6",
        time_budget_seconds=3060,
    )
    _write(
        agent / "official-verifier-result.json",
        {
            "schema": "gt.official_verifier_result.v1",
            "benchmark_suite": "swelive",
            "task_id": SWELIVE_TASK,
            "status": "GRADED",
            "reward": 0,
            "solved": False,
            "failure_class": "graded",
            "error_code": "",
            "product_source_sha": source_sha,
            "product_receipt_present": True,
            "runner_result_path": aggregate_path.relative_to(root).as_posix(),
            "runner_result_sha256": hashlib.sha256(
                aggregate_path.read_bytes()
            ).hexdigest(),
        },
    )
    audit = {
        "schema": "gt.audit.v1",
        "source_sha": source_sha,
        "workflow_run_id": "offline",
        "run_dir": "attestation/tasks",
        "artifact_corpus_sha256": artifact_corpus_sha256(root / "tasks"),
        "tasks": [{"task_name": SWELIVE_TASK, "verdict": "GREEN"}],
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
            "evidence": {
                polarity: {
                    "exit_code": 0,
                    "node_ids": ["tests/provider_free.py::test_fixture"],
                    "execution": {
                        "collected": ["tests/provider_free.py::test_fixture"],
                        "reports": [
                            {"node_id": "tests/provider_free.py::test_fixture",
                             "phase": phase, "outcome": "passed", "wasxfail": False}
                            for phase in ("setup", "call", "teardown")
                        ],
                    },
                }
                for polarity in ("positive", "negative")
            },
            "proof_dimensions": {
                "produced": "tests/provider_free.py",
                "admitted": "tests/provider_free.py",
                "sent_or_correct_quiet": "tests/provider_free.py",
                "behaviorally_relevant": "tests/provider_free.py",
                "negative_or_stale": "tests/provider_free.py",
            },
            "freshness_pins": {"source_revision": source_sha},
            "receipt_digest_sha256": None,
        }
        feature["cell_digest_sha256"] = digest_body(
            feature, field="cell_digest_sha256"
        )
        feature_rows.append(feature)
    feature_matrix = {
        "schema": "gt.feature_matrix.v2",
        "source_revision": source_sha,
        "generated_at": "2026-09-13T00:00:00Z",
        "identity_count": len(feature_rows),
        "rows": feature_rows,
    }
    feature_matrix["matrix_digest_sha256"] = digest_body(
        feature_matrix, field="matrix_digest_sha256"
    )
    _write(root / "feature-matrix.json", feature_matrix)
    return agent / "benchmark-adapter.json"


def test_swelive_gate_one_attests_end_to_end(tmp_path: Path) -> None:
    _swelive_fixture(tmp_path)
    receipt = attest_deepswe(
        tmp_path,
        source_sha="f" * 40,
        task_job_result="success",
        workflow_run_id="offline",
        suite=load_suite("swelive"),
    )
    assert receipt["schema"] == "gt.swelive_gt_harness_attestation.v1", receipt[
        "errors"
    ]
    assert receipt["status"] == "PASS", receipt["errors"]
    assert receipt["task_ids"] == [SWELIVE_TASK]
    assert receipt["graded"] == 1
    assert receipt["solved"] == 0
    assert receipt["official_verifier_tasks"] == [SWELIVE_TASK]


def test_swelive_rejects_a_deepswe_suite_marked_receipt(tmp_path: Path) -> None:
    _swelive_fixture(tmp_path)
    verifier = next(tmp_path.rglob("official-verifier-result.json"))
    row = json.loads(verifier.read_text(encoding="utf-8"))
    row["benchmark_suite"] = "deepswe"
    _write(verifier, row)
    receipt = attest_deepswe(
        tmp_path,
        source_sha="f" * 40,
        task_job_result="success",
        workflow_run_id="offline",
        suite=load_suite("swelive"),
    )
    assert receipt["status"] == "FAIL"
    assert any(
        "official_verifier_suite_mismatch" in error for error in receipt["errors"]
    )


def test_deepswe_suite_still_binds_the_external_snapshot() -> None:
    suite = load_suite("deepswe")
    assert suite.suite_id == "deepswe"
    assert len(suite.canonical_task_ids) == 20
    assert suite.gate_task_id == "aiomonitor-task-snapshots-diff"
    assert suite.remainder_stage == "remaining-19"
    assert suite.plan_filename == "deepswe20-plan.json"
    assert suite.attestation_schema == "gt.deepswe_gt_harness_attestation.v1"
