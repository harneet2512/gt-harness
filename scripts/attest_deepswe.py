#!/usr/bin/env python3
"""Offline, deterministic attestation for a DeepSWE product artifact tree."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
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


def _integer(
    value: object, *, field: str, task: str, errors: list[str]
) -> int:
    if type(value) is not int or value < 0:
        errors.append(f"invalid_product_receipt_field:{task}:{field}")
        return 0
    return value


def _number(
    value: object, *, field: str, task: str, errors: list[str]
) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        errors.append(f"invalid_product_receipt_field:{task}:{field}")
        return 0.0
    return float(value)


def _boolean(
    value: object, *, field: str, task: str, errors: list[str]
) -> bool:
    if type(value) is not bool:
        errors.append(f"invalid_product_receipt_field:{task}:{field}")
        return False
    return value


def _array(
    value: object, *, field: str, task: str, errors: list[str]
) -> list[object]:
    if not isinstance(value, list):
        errors.append(f"invalid_product_receipt_field:{task}:{field}")
        return []
    return value


def _claimed_result(
    root: Path, claimed: object
) -> tuple[Path | None, str]:
    if not isinstance(claimed, str) or not claimed:
        return None, "path_missing"
    relative = Path(claimed)
    if relative.is_absolute() or ".." in relative.parts:
        return None, "path_unsafe"
    candidates = []
    for path in (root / relative, root / "tasks" / relative):
        resolved = path.resolve()
        if resolved.is_relative_to(root.resolve()) and resolved.is_file():
            candidates.append(resolved)
    candidates = list(dict.fromkeys(candidates))
    if len(candidates) != 1:
        return None, "path_ambiguous" if candidates else "result_missing"
    path = candidates[0]
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


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
    if plan.get("schema") != "gt.deepswe_gt_harness_plan.v1":
        errors.append("plan_schema_mismatch")
    if not all(isinstance(task, str) and task for task in expected):
        errors.append("planned_task_identity_invalid")
    if len(expected) != len(expected_set):
        errors.append("duplicate_planned_task")
    if plan.get("task_count") != len(expected):
        errors.append("planned_task_count_mismatch")
    task_order_sha256 = hashlib.sha256(
        ("\n".join(str(task) for task in expected) + "\n").encode("utf-8")
    ).hexdigest()
    if plan.get("task_order_sha256") != task_order_sha256:
        errors.append("planned_task_order_digest_mismatch")
    matrix = plan.get("matrix")
    if not isinstance(matrix, list) or [
        row.get("task") for row in matrix if isinstance(row, dict)
    ] != expected or len(matrix) != len(expected):
        errors.append("planned_task_matrix_mismatch")
    approval = plan.get("paid_run_approval")
    if not isinstance(approval, dict) or (
        approval.get("approved") is not True
        or approval.get("input") != "approve_paid_run"
    ):
        errors.append("paid_run_approval_invalid")
    normalized_job_result = str(task_job_result or "").strip().lower() or "unknown"
    if normalized_job_result != "success":
        errors.append(f"task_job_result_not_success:{normalized_job_result}")
    if provider_gate.get("schema") != "gt.provider_preflight.v1":
        errors.append("provider_gate_schema_mismatch")
    if provider_gate.get("status") != "PASS":
        errors.append("provider_gate_failed")
    if provider_gate.get("source_sha") != source_sha:
        errors.append("provider_gate_source_sha_mismatch")
    if (
        provider_gate.get("mode") != "live"
        or provider_gate.get("provider_ready") is not True
        or provider_gate.get("paid_run_approved") is not True
    ):
        errors.append("provider_gate_live_approval_invalid")
    if (
        provider_gate.get("model") != plan.get("requested_model")
        or provider_gate.get("route_sha256") != plan.get("provider_route_sha256")
    ):
        errors.append("provider_gate_route_mismatch")

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
        try:
            row = _object(path)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            relative = path.relative_to(root).as_posix()
            errors.append(f"invalid_adapter_receipt:{relative}:{type(exc).__name__}")
            continue
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
        try:
            row = _object(path)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            relative = path.relative_to(root).as_posix()
            errors.append(f"invalid_product_receipt:{relative}:{type(exc).__name__}")
            continue
        task = str(row.get("task_id") or "")
        if task in product_runs:
            errors.append(f"duplicate_product_receipt:{task}")
        product_runs[task] = row
        try:
            receipt_errors = verify_runtime_receipt(path)
        except (TypeError, ValueError) as exc:
            errors.append(f"invalid_product_receipt:{task}:{type(exc).__name__}")
            receipt_errors = []
        errors.extend(f"product_receipt:{task}:{reason}" for reason in receipt_errors)
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
        unmet_predicates = _array(
            treatment.get("unmet_predicates"), field="unmet_predicates",
            task=task, errors=errors,
        )
        product_rows.append(
            {
                "task": task,
                "status": row.get("status"),
                "provider_calls": _integer(
                    row.get("provider_calls"), field="provider_calls",
                    task=task, errors=errors,
                ),
                "provider_completed_calls": _integer(
                    row.get("provider_completed_calls"),
                    field="provider_completed_calls", task=task, errors=errors,
                ),
                "provider_failed_calls": _integer(
                    row.get("provider_failed_calls"), field="provider_failed_calls",
                    task=task, errors=errors,
                ),
                "input_tokens": _integer(
                    row.get("input_tokens"), field="input_tokens",
                    task=task, errors=errors,
                ),
                "cached_tokens": _integer(
                    row.get("cached_tokens"), field="cached_tokens",
                    task=task, errors=errors,
                ),
                "output_tokens": _integer(
                    row.get("output_tokens"), field="output_tokens",
                    task=task, errors=errors,
                ),
                "total_cost": _number(
                    row.get("total_cost"), field="total_cost",
                    task=task, errors=errors,
                ),
                "treatment_status": treatment.get("treatment_status"),
                "graph_status": "CERTIFIED" if (
                    graph.get("binary_certified") is True
                    and graph.get("sqlite_quick_check") == "ok"
                ) else "INVALID",
                "delivery_count": _integer(
                    treatment.get("delivery_count"), field="delivery_count",
                    task=task, errors=errors,
                ),
                "verified": _boolean(
                    treatment.get("verified"), field="verified",
                    task=task, errors=errors,
                ),
                "unmet_predicate_count": len(unmet_predicates),
            }
        )
    if set(product_runs) != expected_set:
        errors.append("product_receipt_task_set_mismatch")

    official_results: dict[str, dict[str, Any]] = {}
    for path in (root / "tasks").rglob("agent/official-verifier-result.json"):
        try:
            row = _object(path)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            relative = path.relative_to(root).as_posix()
            errors.append(
                f"invalid_official_verifier:{relative}:{type(exc).__name__}"
            )
            continue
        task = str(row.get("task_id") or "")
        row_errors: list[str] = []
        if row.get("schema") != "gt.official_verifier_result.v1":
            row_errors.append(f"official_verifier_schema_mismatch:{task}")
        if row.get("benchmark_suite") != "deepswe":
            row_errors.append(f"official_verifier_suite_mismatch:{task}")
        status = row.get("status")
        product_receipt_present = row.get("product_receipt_present")
        if type(product_receipt_present) is not bool:
            row_errors.append(
                f"official_verifier_product_receipt_flag_invalid:{task}"
            )
        elif status == "GRADED" and not product_receipt_present:
            row_errors.append(f"official_verifier_product_receipt_missing:{task}")
        result_path, result_digest = _claimed_result(
            root, row.get("runner_result_path")
        )
        if result_path is None:
            row_errors.append(f"official_verifier_result_missing:{task}")
        elif row.get("runner_result_sha256") != result_digest:
            row_errors.append(f"official_verifier_result_digest_mismatch:{task}")
        reward = row.get("reward")
        if status not in {"GRADED", "ERROR"}:
            row_errors.append(f"official_verifier_status_invalid:{task}")
        if status == "GRADED" and (
            isinstance(reward, bool)
            or not isinstance(reward, (int, float))
            or not math.isfinite(reward)
            or reward not in (0, 1)
        ):
            row_errors.append(f"official_verifier_reward_invalid:{task}")
        if status == "ERROR" and reward is not None:
            row_errors.append(f"official_verifier_reward_invalid:{task}")
        if row_errors:
            errors.extend(row_errors)
            continue
        if task in official_results:
            errors.append(f"duplicate_official_verifier:{task}")
        official_results[task] = row
        if row.get("status") != "GRADED":
            errors.append(f"official_verifier_ungraded:{task}")
        if row.get("product_source_sha") != source_sha:
            errors.append(f"official_verifier_source_mismatch:{task}")
    if set(official_results) != expected_set:
        errors.append("official_verifier_task_set_mismatch")

    outcome_tasks = list(dict.fromkeys(expected))
    expected_official = {
        task: row for task, row in official_results.items() if task in expected_set
    }
    try:
        outcomes = conservative_outcomes(outcome_tasks, expected_official)
    except ValueError as exc:
        errors.append(f"outcome_conservation_failed:{exc}")
        outcomes = conservative_outcomes(outcome_tasks, {})
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
    try:
        receipt = attest_deepswe(
            args.root,
            source_sha=args.source_sha,
            task_job_result=args.task_job_result,
            workflow_run_id=args.workflow_run_id,
        )
    except Exception as exc:  # noqa: BLE001 - the attestation must always be durable
        cause = {
            FileNotFoundError: "required_artifact_missing",
            json.JSONDecodeError: "artifact_json_malformed",
            KeyError: "required_field_missing",
            TypeError: "artifact_type_invalid",
            ValueError: "artifact_value_invalid",
        }.get(type(exc), "unexpected_construction_failure")
        evidence_ref = ""
        filename = getattr(exc, "filename", None)
        if filename:
            try:
                evidence_ref = Path(filename).resolve().relative_to(
                    args.root.resolve()
                ).as_posix()
            except ValueError:
                evidence_ref = Path(filename).name
        receipt = {
            "schema": "gt.deepswe_gt_harness_attestation_error.v1",
            "status": "FAIL",
            "workflow_run_id": args.workflow_run_id,
            "source_sha": args.source_sha,
            "task_job_result": args.task_job_result,
            "task_count": 0,
            "task_ids": [],
            "graded": 0,
            "solved": 0,
            "outcomes": {},
            "errors": [f"attestation_construction_failed:{cause}"],
            "primary_error": {
                "code": "attestation_construction_failed",
                "cause": cause,
                "exception_type": type(exc).__name__,
                "evidence_ref": evidence_ref,
                "recovery": "repair_or_restore_the_named_artifact_and_rerun_attestation",
            },
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(receipt, allow_nan=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt, allow_nan=False, indent=2, sort_keys=True))
    return 0 if receipt["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
