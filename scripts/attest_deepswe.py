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

from gt_engine.feature_matrix import verify_matrix
from gt_harness.runtime_receipts import verify_runtime_receipt
from scripts.gt_audit import artifact_corpus_sha256, audit_digest_sha256
from scripts.provider_preflight import load_route
from scripts.smoke_stage import GATE_STAGE, GATE_TASK_ID, REMAINDER_STAGE, select_stage_tasks
from scripts.standardize_benchmark_result import (
    _failure_class,
    _reward,
    conservative_outcomes,
)

ROOT = Path(__file__).resolve().parents[1]
CANONICAL_TASK_IDS = tuple(
    json.loads(
        (ROOT / "eval" / "deepswe_smoke20_v1.json").read_text(encoding="utf-8")
    )["task_ids"]
)


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
    root: Path, claimed: object, *, expected: Path | None
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
    if path.name != "result.json" or expected is None or path != expected.resolve():
        return None, "result_not_canonical"
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def _required_evidence(
    path: Path, *, missing: str, invalid: str, errors: list[str]
) -> dict[str, Any] | None:
    try:
        return _object(path)
    except FileNotFoundError:
        errors.append(missing)
    except (OSError, json.JSONDecodeError, ValueError):
        errors.append(invalid)
    return None


def _digest_without(payload: dict[str, Any], field: str) -> str:
    body = {key: value for key, value in payload.items() if key != field}
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _total_cost(rows: list[dict[str, Any]], errors: list[str]) -> float:
    value = sum(row["total_cost"] for row in rows)
    if not math.isfinite(value):
        errors.append("product_total_cost_overflow")
        return 0.0
    return round(value, 12)


def attest_deepswe(
    root: Path, *, source_sha: str, task_job_result: str, workflow_run_id: str
) -> dict[str, Any]:
    """Conservatively bind every planned task to runtime and grader evidence."""

    plan = _object(root / "deepswe20-plan.json")
    provider_gate = _object(root / "provider-gate.json")
    errors: list[str] = []
    task_ids = plan.get("task_ids")
    if not isinstance(task_ids, list):
        raise ValueError("planned_task_ids_not_array")
    expected = list(task_ids)
    expected_set = set(expected)
    cohort_stage = plan.get("cohort_stage")
    if cohort_stage is None:
        expected_cohort = list(CANONICAL_TASK_IDS)
    else:
        try:
            expected_cohort = select_stage_tasks(list(CANONICAL_TASK_IDS), cohort_stage)
        except ValueError:
            expected_cohort = []
            errors.append("planned_cohort_stage_invalid")
        if plan.get("gate_task_id") != GATE_TASK_ID:
            errors.append("planned_gate_task_mismatch")
        if plan.get("full_task_count") != len(CANONICAL_TASK_IDS):
            errors.append("planned_full_task_count_mismatch")
        full_order_hash = hashlib.sha256(
            ("\n".join(CANONICAL_TASK_IDS) + "\n").encode("utf-8")
        ).hexdigest()
        if plan.get("full_task_order_sha256") != full_order_hash:
            errors.append("planned_full_task_order_digest_mismatch")
        if cohort_stage == GATE_STAGE and plan.get("prior_gate") is not None:
            errors.append("gate_one_prior_gate_unexpected")
        if cohort_stage == REMAINDER_STAGE:
            binding = plan.get("prior_gate")
            if (
                not isinstance(binding, dict)
                or binding.get("schema") != "gt.prior_gate_binding.v1"
                or binding.get("task_id") != GATE_TASK_ID
                or binding.get("source_sha") != source_sha
                or type(binding.get("workflow_run_id")) is not int
                or not all(
                    isinstance(binding.get(key), str)
                    and len(binding[key]) == 64
                    and all(char in "0123456789abcdef" for char in binding[key])
                    for key in ("attestation_sha256", "diagnostic_summary_sha256")
                )
            ):
                errors.append("prior_gate_binding_invalid")
    if expected != expected_cohort:
        errors.append("planned_canonical_cohort_mismatch")
    if plan.get("schema") != "gt.deepswe_gt_harness_plan.v1":
        errors.append("plan_schema_mismatch")
    if not all(isinstance(task, str) and task for task in expected):
        errors.append("planned_task_identity_invalid")
    if len(expected) != len(expected_set):
        errors.append("duplicate_planned_task")
    if type(plan.get("task_count")) is not int or plan.get("task_count") != len(expected):
        errors.append("planned_task_count_mismatch")
    task_order_sha256 = hashlib.sha256(
        ("\n".join(str(task) for task in expected) + "\n").encode("utf-8")
    ).hexdigest()
    if plan.get("task_order_sha256") != task_order_sha256:
        errors.append("planned_task_order_digest_mismatch")
    matrix_value = plan.get("matrix")
    matrix = matrix_value if isinstance(matrix_value, list) else []
    if (
        not isinstance(matrix_value, list)
        or not all(isinstance(row, dict) for row in matrix)
        or [row.get("task") for row in matrix if isinstance(row, dict)] != expected
        or len(matrix) != len(expected)
    ):
        errors.append("planned_task_matrix_mismatch")
    trusted_bundle = _object(ROOT / "config" / "deepswe_product_bundle_v1.json")
    trusted_manifest = _object(ROOT / "eval" / "deepswe_smoke20_v1.json")
    trusted_tasks = {
        row.get("task_id"): row
        for row in trusted_bundle.get("tasks", [])
        if isinstance(row, dict)
    }
    if (
        plan.get("benchmark_sha") != trusted_manifest.get("benchmark_sha")
        or plan.get("benchmark_sha") != trusted_bundle.get("dataset", {}).get("commit")
        or plan.get("task_config_identity")
        != trusted_bundle.get("dataset", {}).get("task_config_identity")
        or not expected_set.issubset(set(trusted_manifest.get("task_ids", [])))
    ):
        errors.append("planned_benchmark_identity_mismatch")
    expected_budgets: dict[str, str] = {}
    observed_languages: dict[str, int] = {}
    for ordinal, row in enumerate(matrix, start=1):
        if not isinstance(row, dict):
            continue
        task = row.get("task")
        trusted = trusted_tasks.get(task)
        language = row.get("language")
        time_budget = row.get("time_budget_seconds")
        outer_budget = row.get("outer_agent_timeout_seconds")
        if (
            trusted is None
            or type(row.get("ordinal")) is not int
            or row.get("ordinal") != ordinal
            or not isinstance(language, str)
            or not language
            or language != trusted.get("language")
            or type(time_budget) is not int
            or time_budget < 30
            or type(outer_budget) is not int
            or outer_budget <= time_budget
            or row.get("task_config_sha256") != trusted.get("task_config_sha256")
            or row.get("container_image") != trusted.get("container_image")
            or row.get("container_digest") != trusted.get("container_digest")
        ):
            errors.append(f"planned_task_provenance_mismatch:{task}")
            continue
        expected_budgets[str(task)] = str(time_budget)
        observed_languages[language] = observed_languages.get(language, 0) + 1
    if plan.get("language_counts") != observed_languages:
        errors.append("planned_language_counts_mismatch")
    if (
        plan.get("attempts_per_task") != 1
        or type(plan.get("max_parallel")) is not int
        or plan.get("max_parallel") != min(20, len(expected))
        or plan.get("agent")
        != "eval.pier_gt_harness_adapter:PierGtHarnessMiniSwe246Agent"
        or plan.get("agent_scaffold") != "mini-swe-agent"
        or plan.get("agent_scaffold_version") != "2.4.6"
        or plan.get("treatment") != "groundtruth"
    ):
        errors.append("planned_execution_contract_mismatch")
    trusted_route, trusted_route_digest = load_route(
        ROOT / "config" / "provider_route.v1.json"
    )
    if (
        plan.get("provider_route_id") != trusted_route.get("route_id")
        or plan.get("provider_route_sha256") != trusted_route_digest
        or plan.get("provider") != trusted_route.get("provider")
        or plan.get("provider_base_url") != trusted_route.get("base_url")
        or plan.get("requested_model") != trusted_route.get("model")
        or plan.get("effective_model") != f"openai/{trusted_route.get('model')}"
    ):
        errors.append("planned_provider_route_mismatch")
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
    provider_gate_fields = {
        "schema", "status", "error_code", "mode", "source_sha", "route_id",
        "provider", "base_url", "model", "route_sha256", "checks",
        "provider_ready", "paid_run_approved", "account_amounts_recorded",
        "provider_inference_attempts", "provider_inference_calls",
        "context_window_tokens", "reserved_output_tokens", "context_window_source",
    }
    if set(provider_gate) != provider_gate_fields:
        errors.append("provider_gate_fields_invalid")
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
        provider_gate.get("route_id") != trusted_route.get("route_id")
        or provider_gate.get("provider") != trusted_route.get("provider")
        or provider_gate.get("base_url") != trusted_route.get("base_url")
        or provider_gate.get("model") != trusted_route.get("model")
        or provider_gate.get("route_sha256") != trusted_route_digest
    ):
        errors.append("provider_gate_route_mismatch")
    checks = provider_gate.get("checks")
    required_provider_checks = {
        "credential_valid",
        "key_limit_available",
        "model_visible",
        "model_canary_served",
    }
    if (
        not isinstance(checks, dict)
        or set(checks) != required_provider_checks
        or any(checks.get(key) is not True for key in required_provider_checks)
        or provider_gate.get("error_code") is not None
        or provider_gate.get("account_amounts_recorded") is not False
        or type(provider_gate.get("provider_inference_attempts")) is not int
        or provider_gate.get("provider_inference_attempts") != 1
        or type(provider_gate.get("provider_inference_calls")) is not int
        or provider_gate.get("provider_inference_calls") != 1
    ):
        errors.append("provider_gate_checks_invalid")
    context_window = provider_gate.get("context_window_tokens")
    reserved_output = provider_gate.get("reserved_output_tokens")
    if (
        type(context_window) is not int
        or type(reserved_output) is not int
        or context_window <= reserved_output
        or reserved_output != trusted_route.get("requested_output_tokens")
        or provider_gate.get("context_window_source") != "openrouter:/models"
    ):
        errors.append("provider_gate_admission_invalid")

    result_rows: list[tuple[Path, dict[str, Any]]] = []
    trial_rows: list[tuple[Path, dict[str, Any]]] = []
    for path in (root / "tasks").rglob("result.json"):
        try:
            row = _object(path)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            errors.append(f"invalid_result:{path}:{type(exc).__name__}")
            continue
        result_rows.append((path, row))
        if row.get("task_name") and row.get("trial_name"):
            trial_rows.append((path, row))
    observed_trials = [_task_name(row["task_name"]) for _, row in trial_rows]
    if len(observed_trials) != len(expected) or set(observed_trials) != expected_set:
        errors.append("trial_task_set_mismatch")
    if len(observed_trials) != len(set(observed_trials)):
        errors.append("duplicate_trial_task")
    trial_evidence = {
        _task_name(row["task_name"]): (path, row) for path, row in trial_rows
    }

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
        verified = _boolean(
            treatment.get("verified"), field="verified",
            task=task, errors=errors,
        )
        if not verified:
            errors.append(f"product_completion_unverified:{task}")
        if unmet_predicates:
            errors.append(f"product_unmet_predicates:{task}")
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
                "verified": verified,
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
        elif product_receipt_present != (task in product_runs):
            row_errors.append(f"official_verifier_product_receipt_mismatch:{task}")
        trial_path, trial_row = trial_evidence.get(task, (None, None))
        aggregate_candidates = [
            (candidate_path, candidate_row)
            for candidate_path, candidate_row in result_rows
            if candidate_path != trial_path
            and "stats" in candidate_row
            and "n_total_trials" in candidate_row
            and trial_path is not None
            and trial_path.is_relative_to(candidate_path.parent)
        ]
        if len(aggregate_candidates) != 1:
            row_errors.append(f"official_verifier_result_ambiguous:{task}")
            expected_result_path = None
            expected_result_row: dict[str, Any] = {}
        else:
            expected_result_path, expected_result_row = aggregate_candidates[0]
        result_path, result_digest = _claimed_result(
            root, row.get("runner_result_path"), expected=expected_result_path
        )
        if result_path is None:
            row_errors.append(f"official_verifier_result_not_canonical:{task}")
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
        try:
            aggregate_reward = _reward(expected_result_row)
            trial_reward = _reward(trial_row or {})
            recomputed_reward = (
                aggregate_reward
                if aggregate_reward is not None and aggregate_reward == trial_reward
                else None
            )
        except (AttributeError, TypeError, ValueError):
            recomputed_reward = None
            row_errors.append(f"official_verifier_result_malformed:{task}")
        if status == "GRADED" and (
            recomputed_reward is None
            or reward != recomputed_reward
            or row.get("solved") is not (recomputed_reward == 1)
        ):
            row_errors.append(f"official_verifier_recomputation_mismatch:{task}")
        if status == "ERROR":
            try:
                expected_failure, expected_error = _failure_class(
                    trial_row, runner_result_present=expected_result_path is not None
                )
            except (AttributeError, TypeError, ValueError):
                expected_failure, expected_error = "", ""
                row_errors.append(f"official_verifier_result_malformed:{task}")
            if (
                recomputed_reward is not None
                or row.get("solved") is not None
                or row.get("failure_class") != expected_failure
                or row.get("error_code") != expected_error
            ):
                row_errors.append(f"official_verifier_recomputation_mismatch:{task}")
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

    audit = _required_evidence(
        root / "gt-audit.json",
        missing="canonical_audit_missing",
        invalid="canonical_audit_invalid",
        errors=errors,
    )
    if audit is not None:
        audit_tasks = audit.get("tasks")
        audit_names = [
            row.get("task_name")
            for row in audit_tasks
            if isinstance(row, dict)
        ] if isinstance(audit_tasks, list) else []
        if (
            audit.get("schema") != "gt.audit.v1"
            or audit.get("source_sha") != source_sha
            or audit.get("workflow_run_id") != workflow_run_id
            or str(audit.get("run_dir", "")).replace("\\", "/")
            != "attestation/tasks"
            or audit.get("artifact_corpus_sha256")
            != artifact_corpus_sha256(root / "tasks")
            or audit.get("audit_digest_sha256") != audit_digest_sha256(audit)
            or not isinstance(audit_tasks, list)
            or len(audit_tasks) != len(expected)
            or len(audit_names) != len(expected)
            or set(audit_names) != expected_set
            or any(
                row.get("verdict") not in {
                    "GREEN",
                    "GREEN-quiet",
                    "GREEN-dormant",
                    "GREEN-delivered",
                }
                for row in audit_tasks
                if isinstance(row, dict)
            )
        ):
            errors.append("canonical_audit_failed_or_incomplete")
    live_gate = _required_evidence(
        root / "gt-live-gate.json",
        missing="canonical_live_gate_missing",
        invalid="canonical_live_gate_invalid",
        errors=errors,
    )
    if live_gate is not None and (
        live_gate.get("schema") != "gt.live_acceptance.v1"
        or live_gate.get("passed") is not True
        or type(live_gate.get("task_count")) is not int
        or live_gate.get("task_count") != len(expected)
        or type(live_gate.get("expected_tasks")) is not int
        or live_gate.get("expected_tasks") != len(expected)
        or live_gate.get("expected_model") != plan.get("requested_model")
        or live_gate.get("observed_models") != [plan.get("requested_model")]
        or live_gate.get("issues") != []
        or live_gate.get("source_sha") != source_sha
        or live_gate.get("workflow_run_id") != workflow_run_id
        or live_gate.get("audit_digest_sha256")
        != (audit or {}).get("audit_digest_sha256")
        or live_gate.get("audit_file_sha256")
        != (
            hashlib.sha256((root / "gt-audit.json").read_bytes()).hexdigest()
            if audit is not None else None
        )
        or live_gate.get("report_digest_sha256")
        != _digest_without(live_gate, "report_digest_sha256")
    ):
        errors.append("canonical_live_gate_failed_or_incomplete")
    feature_matrix = _required_evidence(
        root / "feature-matrix.json",
        missing="canonical_feature_matrix_missing",
        invalid="canonical_feature_matrix_invalid",
        errors=errors,
    )
    if feature_matrix is not None:
        feature_errors = verify_matrix(
            feature_matrix, expected_source_revision=source_sha
        )
        feature_rows = feature_matrix.get("rows")
        if isinstance(feature_rows, list):
            witnessed = True
            for row in feature_rows:
                if not isinstance(row, dict):
                    witnessed = False
                    continue
                evidence = row.get("evidence")
                freshness = row.get("freshness_pins")
                witnessed = witnessed and (
                    row.get("disposition") == "WITNESSED"
                    and isinstance(evidence, dict)
                    and evidence.get("exit_code") == 0
                    and isinstance(freshness, dict)
                    and freshness.get("source_revision") == source_sha
                )
            if (
                type(feature_matrix.get("identity_count")) is not int
                or feature_matrix.get("identity_count") != len(feature_rows)
                or not witnessed
            ):
                feature_errors.append("feature evidence is not freshly witnessed")
        if feature_errors:
            errors.extend(
                f"canonical_feature_matrix:{reason}" for reason in feature_errors
            )

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
    totals["total_cost"] = _total_cost(product_rows, errors)
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
        "provider_gate": {
            key: (
                {
                    check: (provider_gate.get("checks") or {}).get(check) is True
                    for check in sorted(required_provider_checks)
                }
                if key == "checks" else provider_gate.get(key)
            )
            for key in sorted(provider_gate_fields)
        },
        "canonical_evidence": {
            name: {
                "artifact_ref": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()
                if path.is_file() else None,
            }
            for name, path in {
                "audit": root / "gt-audit.json",
                "live_gate": root / "gt-live-gate.json",
                "feature_matrix": root / "feature-matrix.json",
            }.items()
        },
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
        fallback_tasks = list(CANONICAL_TASK_IDS)
        receipt = {
            "schema": "gt.deepswe_gt_harness_attestation_error.v1",
            "status": "FAIL",
            "workflow_run_id": args.workflow_run_id,
            "source_sha": args.source_sha,
            "task_job_result": args.task_job_result,
            "task_count": len(fallback_tasks),
            "task_ids": fallback_tasks,
            "graded": 0,
            "solved": 0,
            "outcomes": conservative_outcomes(fallback_tasks, {}),
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
