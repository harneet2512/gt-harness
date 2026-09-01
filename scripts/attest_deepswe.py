#!/usr/bin/env python3
"""Offline, deterministic attestation for a DeepSWE product artifact tree."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from gt_harness.runtime_receipts import verify_runtime_receipt
from scripts.standardize_benchmark_result import conservative_outcomes


def _object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"object_required:{path}")
    return value


def _task_name(value: object) -> str:
    return str(value).split("__", 1)[0].rsplit("/", 1)[-1]


def attest_deepswe(
    root: Path, *, source_sha: str, task_job_result: str, workflow_run_id: str
) -> dict[str, Any]:
    """Conservatively bind every planned task to runtime and grader evidence."""

    plan = _object(root / "deepswe20-plan.json")
    provider_gate = _object(root / "provider-gate.json")
    expected = list(plan["task_ids"])
    expected_set = set(expected)
    expected_budgets = {
        row["task"]: str(row["time_budget_seconds"]) for row in plan["matrix"]
    }
    errors: list[str] = []
    if provider_gate.get("status") != "PASS":
        errors.append("provider_gate_failed")
    if provider_gate.get("source_sha") != source_sha:
        errors.append("provider_gate_source_sha_mismatch")

    trial_rows: list[dict[str, Any]] = []
    for path in (root / "tasks").rglob("result.json"):
        try:
            row = _object(path)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            errors.append(f"invalid_result:{path}:{type(exc).__name__}")
            continue
        if row.get("task_name") and row.get("trial_name"):
            trial_rows.append(row)
    observed_trials = [_task_name(row["task_name"]) for row in trial_rows]
    if len(observed_trials) != len(expected) or set(observed_trials) != expected_set:
        errors.append("trial_task_set_mismatch")
    if len(observed_trials) != len(set(observed_trials)):
        errors.append("duplicate_trial_task")

    adapters: dict[str, dict[str, Any]] = {}
    for path in (root / "tasks").rglob("agent/benchmark-adapter.json"):
        row = _object(path)
        task = str(row.get("task_id") or "")
        if task in adapters:
            errors.append(f"duplicate_adapter_receipt:{task}")
        adapters[task] = row
        if row.get("product_command") != "gt-miniswe-run" or row.get("attempt") != 1:
            errors.append(f"adapter_contract_mismatch:{task}")
        if row.get("treatment") != plan.get("treatment"):
            errors.append(f"adapter_treatment_mismatch:{task}")
        if row.get("requested_model") != plan.get("requested_model"):
            errors.append(f"requested_model_mismatch:{task}")
        if row.get("effective_model") != plan.get("effective_model"):
            errors.append(f"effective_model_mismatch:{task}")
        if row.get("agent_scaffold_version") != "2.4.6":
            errors.append(f"scaffold_version_mismatch:{task}")
        if row.get("product_source_sha") != source_sha:
            errors.append(f"adapter_source_sha_mismatch:{task}")
        if str(row.get("time_budget_seconds")) != expected_budgets.get(task):
            errors.append(f"adapter_time_budget_mismatch:{task}")
    if set(adapters) != expected_set:
        errors.append("adapter_receipt_task_set_mismatch")

    product_runs: dict[str, dict[str, Any]] = {}
    product_rows: list[dict[str, Any]] = []
    for path in (root / "tasks").rglob("agent/gt-run.json"):
        row = _object(path)
        task = str(row.get("task_id") or "")
        if task in product_runs:
            errors.append(f"duplicate_product_receipt:{task}")
        product_runs[task] = row
        errors.extend(f"product_receipt:{task}:{reason}" for reason in verify_runtime_receipt(path))
        for field, expected_value in (
            ("product_source_sha", source_sha),
            ("requested_model", plan.get("requested_model")),
            ("effective_model", plan.get("effective_model")),
        ):
            if row.get(field) != expected_value:
                errors.append(f"product_{field}_mismatch:{task}")
        if str(row.get("time_budget_seconds")) != expected_budgets.get(task):
            errors.append(f"product_time_budget_mismatch:{task}")
        treatment = row.get("treatment_receipt")
        if not isinstance(treatment, dict):
            errors.append(f"missing_treatment_receipt:{task}")
            continue
        graph = treatment.get("graph_certification") or {}
        product_rows.append(
            {
                "task": task,
                "status": row.get("status"),
                "provider_calls": int(row.get("provider_calls") or 0),
                "provider_completed_calls": int(row.get("provider_completed_calls") or 0),
                "provider_failed_calls": int(row.get("provider_failed_calls") or 0),
                "input_tokens": int(row.get("input_tokens") or 0),
                "cached_tokens": int(row.get("cached_tokens") or 0),
                "output_tokens": int(row.get("output_tokens") or 0),
                "total_cost": float(row.get("total_cost") or 0),
                "treatment_status": treatment.get("treatment_status"),
                "graph_status": "CERTIFIED" if (
                    graph.get("binary_certified") is True
                    and graph.get("sqlite_quick_check") == "ok"
                ) else "INVALID",
                "delivery_count": int(treatment.get("delivery_count") or 0),
                "verified": bool(treatment.get("verified")),
                "unmet_predicate_count": len(treatment.get("unmet_predicates") or []),
            }
        )
    if set(product_runs) != expected_set:
        errors.append("product_receipt_task_set_mismatch")

    official_results: dict[str, dict[str, Any]] = {}
    for path in (root / "tasks").rglob("agent/official-verifier-result.json"):
        row = _object(path)
        task = str(row.get("task_id") or "")
        if task in official_results:
            errors.append(f"duplicate_official_verifier:{task}")
        official_results[task] = row
        if row.get("status") != "GRADED":
            errors.append(f"official_verifier_ungraded:{task}")
        if row.get("product_source_sha") != source_sha:
            errors.append(f"official_verifier_source_mismatch:{task}")
    if set(official_results) != expected_set:
        errors.append("official_verifier_task_set_mismatch")

    outcomes = conservative_outcomes(expected, official_results)
    if source_sha != plan.get("source_sha"):
        errors.append("source_sha_mismatch")
    totals = {
        key: sum(row[key] for row in product_rows)
        for key in (
            "provider_calls", "provider_completed_calls", "provider_failed_calls",
            "input_tokens", "cached_tokens", "output_tokens", "delivery_count",
        )
    }
    totals["total_cost"] = round(sum(row["total_cost"] for row in product_rows), 12)
    return {
        "schema": "gt.deepswe_gt_harness_attestation.v1",
        "status": "PASS" if not errors else "FAIL",
        "workflow_run_id": workflow_run_id,
        "source_sha": source_sha,
        "benchmark_sha": plan["benchmark_sha"],
        "task_job_result": task_job_result,
        "task_count": plan["task_count"],
        "task_ids": expected,
        "task_order_sha256": plan["task_order_sha256"],
        "language_counts": plan["language_counts"],
        "requested_model": plan["requested_model"],
        "effective_model": plan["effective_model"],
        "agent": plan["agent"],
        "agent_scaffold_version": plan["agent_scaffold_version"],
        "treatment": plan["treatment"],
        "provider_gate": provider_gate,
        "paid_run_approval": plan["paid_run_approval"],
        "baseline": plan["baseline"],
        "graded": sum(1 for row in outcomes.values() if row["graded"]),
        "solved": sum(1 for row in outcomes.values() if row["solved"]),
        "outcomes": outcomes,
        "official_verifier_tasks": sorted(official_results),
        "product_totals": totals,
        "product_rows": sorted(product_rows, key=lambda row: row["task"]),
        "observed_trial_tasks": sorted(observed_trials),
        "adapter_receipt_tasks": sorted(adapters),
        "product_receipt_tasks": sorted(product_runs),
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--task-job-result", required=True)
    parser.add_argument("--workflow-run-id", default=os.environ.get("GITHUB_RUN_ID", "offline"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    receipt = attest_deepswe(
        args.root,
        source_sha=args.source_sha,
        task_job_result=args.task_job_result,
        workflow_run_id=args.workflow_run_id,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
