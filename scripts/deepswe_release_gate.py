#!/usr/bin/env python3
"""Fail-closed outcome and efficiency gate for a frozen DeepSWE A/B pair."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

IDENTITY_FIELDS = (
    "benchmark_sha",
    "model",
    "provider",
    "temperature",
    "step_limit",
    "execution_budget_sec",
    "runner",
    "workspace_prompt_contract",
    "provider_response_models",
    "provider_response_providers",
    "provider_system_fingerprints",
    "miniswe_version",
    "executor_retry_policy",
    "protocol_class",
    "rollouts_per_task",
    "leaderboard_equivalent",
)
ROW_IDENTITY_FIELDS = (
    "system_prompt_sha256",
    "task_prompt_sha256",
    "tool_schema_sha256",
    "executor_response_models",
    "executor_response_providers",
    "executor_system_fingerprints",
)
RESOURCE_FIELDS = (
    "total_tokens",
    "provider_calls",
    "decision_actions",
    "effective_actions",
    "assistant_steps",
    "wall_time_sec",
    "provider_cost_usd",
)
SETUP_RESOURCE_FIELDS = (
    "provider_calls",
    "input_tokens",
    "output_tokens",
    "cost_usd",
    "latency_ms",
)


@dataclass(frozen=True, slots=True)
class DeepSweReleaseReport:
    phase: str
    passed: bool
    failures: tuple[str, ...]
    baseline_solved: int
    treatment_solved: int
    losses: tuple[str, ...]
    flips: tuple[str, ...]
    common_solved: tuple[str, ...]
    common_solved_deltas: dict[str, float]
    task_resource_deltas: dict[str, float]
    all_in_resource_deltas: dict[str, float]
    benchmark_setup_overhead: dict[str, float]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _rows(arm: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for row in arm.get("rows") or ():
        if not isinstance(row, dict):
            continue
        task = str(row.get("task") or "").strip()
        if task and task not in rows:
            rows[task] = row
    return rows


def _uncensored_solved(row: dict[str, Any]) -> bool:
    return bool(row.get("solved") is True and not row.get("exception"))


def _verifier_outcome_valid(row: dict[str, Any]) -> bool:
    rewards = row.get("rewards") or {}
    reward = rewards.get("reward") if isinstance(rewards, dict) else None
    return bool(
        isinstance(row.get("solved"), bool)
        and not isinstance(reward, bool)
        and isinstance(reward, (int, float))
        and math.isfinite(float(reward))
        and float(reward) in {0.0, 1.0}
        and row["solved"] is (float(reward) == 1.0 and not row.get("exception"))
    )


def _number(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0.0:
        return None
    return numeric


def assess_deepswe_release(
    baseline: dict[str, Any],
    treatment: dict[str, Any],
    *,
    phase: str,
) -> DeepSweReleaseReport:
    """Assess a matched pair without inferring missing evidence as success."""

    if phase not in {"preservation", "promotion"}:
        raise ValueError("phase must be preservation or promotion")
    failures: list[str] = []
    if baseline.get("schema") != treatment.get("schema"):
        failures.append("schema_mismatch")
    baseline_manifest = baseline.get("manifest") or {}
    treatment_manifest = treatment.get("manifest") or {}
    for field in IDENTITY_FIELDS:
        if field not in baseline_manifest or field not in treatment_manifest:
            failures.append(f"manifest_missing:{field}")
        elif baseline_manifest[field] != treatment_manifest[field]:
            failures.append(f"manifest_mismatch:{field}")
    gt_commit = str(treatment_manifest.get("gt_commit") or "")
    if len(gt_commit) != 40:
        failures.append("treatment_gt_commit_not_exact")
    if (
        baseline_manifest.get("arm") != "gt_off"
        or baseline_manifest.get("integration_mode") != "off"
        or baseline_manifest.get("comparison_profile") != "baseline"
        or baseline_manifest.get("claim_scope") != "control"
    ):
        failures.append("baseline_arm_not_off")
    if (
        treatment_manifest.get("arm") != "gt_on"
        or treatment_manifest.get("integration_mode") != "active"
        or treatment_manifest.get("comparison_profile") != "certified_full"
        or treatment_manifest.get("claim_scope") != "integrated_groundtruth_product"
    ):
        failures.append("treatment_arm_not_certified_full")
    for manifest in (baseline_manifest, treatment_manifest):
        if not manifest.get("provider_system_fingerprints"):
            failures.append("observed_fingerprint_missing:manifest")

    baseline_rows = _rows(baseline)
    treatment_rows = _rows(treatment)
    if set(baseline_rows) != set(treatment_rows):
        failures.append("task_set_mismatch")
    for task, row in (*baseline_rows.items(), *treatment_rows.items()):
        for metric in RESOURCE_FIELDS:
            if _number(row, metric) is None:
                failures.append(f"missing_metric:{task}:{metric}")
    for task in sorted(set(baseline_rows) & set(treatment_rows)):
        for field in ROW_IDENTITY_FIELDS:
            if field not in baseline_rows[task] or field not in treatment_rows[task]:
                failures.append(f"row_identity_missing:{task}:{field}")
            elif baseline_rows[task][field] != treatment_rows[task][field]:
                failures.append(f"row_identity_mismatch:{task}:{field}")
        if not baseline_rows[task].get("executor_system_fingerprints") or not treatment_rows[
            task
        ].get("executor_system_fingerprints"):
            failures.append(f"observed_fingerprint_missing:{task}")
        if baseline_rows[task].get("integration_mode") != "off":
            failures.append(f"baseline_row_not_off:{task}")
        if treatment_rows[task].get("integration_mode") != "active":
            failures.append(f"treatment_row_not_active:{task}")
        if baseline_rows[task].get("exception"):
            failures.append(f"censored_baseline:{task}")
        if treatment_rows[task].get("exception"):
            failures.append(f"censored_treatment:{task}")
        if not isinstance(baseline_rows[task].get("solved"), bool):
            failures.append(f"outcome_invalid:baseline:{task}")
        if not isinstance(treatment_rows[task].get("solved"), bool):
            failures.append(f"outcome_invalid:treatment:{task}")
        if not _verifier_outcome_valid(baseline_rows[task]):
            failures.append(f"verifier_outcome_invalid:baseline:{task}")
        if not _verifier_outcome_valid(treatment_rows[task]):
            failures.append(f"verifier_outcome_invalid:treatment:{task}")

    raw_setup = treatment.get("benchmark_setup_overhead")
    setup_overhead: dict[str, float] = {}
    if not isinstance(raw_setup, dict):
        failures.append("benchmark_setup_overhead_missing")
    else:
        for field in SETUP_RESOURCE_FIELDS:
            value = raw_setup.get(field)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or float(value) < 0.0
            ):
                failures.append(f"benchmark_setup_overhead_invalid:{field}")
            else:
                setup_overhead[field] = float(value)
        if setup_overhead.get("provider_calls") != 1.0:
            failures.append("benchmark_setup_provider_call_count")
        for field in ("input_tokens", "output_tokens", "cost_usd", "latency_ms"):
            if setup_overhead.get(field, 0.0) <= 0.0:
                failures.append(f"benchmark_setup_overhead_nonpositive:{field}")
    comparable_tasks = {
        task
        for task in set(baseline_rows) & set(treatment_rows)
        if not baseline_rows[task].get("exception")
        and not treatment_rows[task].get("exception")
        and isinstance(baseline_rows[task].get("solved"), bool)
        and isinstance(treatment_rows[task].get("solved"), bool)
        and _verifier_outcome_valid(baseline_rows[task])
        and _verifier_outcome_valid(treatment_rows[task])
    }
    baseline_solved_tasks = {
        task for task in comparable_tasks if _uncensored_solved(baseline_rows[task])
    }
    treatment_solved_tasks = {
        task for task in comparable_tasks if _uncensored_solved(treatment_rows[task])
    }
    losses = tuple(sorted(baseline_solved_tasks - treatment_solved_tasks))
    flips = tuple(sorted(treatment_solved_tasks - baseline_solved_tasks))
    common = tuple(sorted(baseline_solved_tasks & treatment_solved_tasks))
    failures.extend(f"baseline_solve_regression:{task}" for task in losses)

    task_deltas: dict[str, float] = {}
    for metric in RESOURCE_FIELDS:
        baseline_values = [_number(baseline_rows[task], metric) for task in common]
        treatment_values = [_number(treatment_rows[task], metric) for task in common]
        if any(value is None for value in (*baseline_values, *treatment_values)):
            task_deltas[metric] = 0.0
            continue
        delta = sum(value or 0.0 for value in treatment_values) - sum(
            value or 0.0 for value in baseline_values
        )
        task_deltas[metric] = round(delta, 9)

    setup_by_resource = {
        "total_tokens": setup_overhead.get("input_tokens", 0.0)
        + setup_overhead.get("output_tokens", 0.0),
        "provider_calls": setup_overhead.get("provider_calls", 0.0),
        "decision_actions": 0.0,
        "effective_actions": 0.0,
        "assistant_steps": 0.0,
        "wall_time_sec": setup_overhead.get("latency_ms", 0.0) / 1_000.0,
        "provider_cost_usd": setup_overhead.get("cost_usd", 0.0),
    }
    all_in_deltas = {
        metric: round(task_deltas.get(metric, 0.0) + setup_by_resource[metric], 9)
        for metric in RESOURCE_FIELDS
    }
    for metric, delta in task_deltas.items():
        if delta > 0:
            failures.append(f"common_solved_resource_regression:{metric}")
    for metric, delta in all_in_deltas.items():
        if delta > 0:
            failures.append(f"all_in_resource_regression:{metric}")

    if phase == "promotion":
        if not flips:
            failures.append("no_positive_flip")
        if len(treatment_solved_tasks) <= len(baseline_solved_tasks):
            failures.append("no_net_solve_improvement")

    return DeepSweReleaseReport(
        phase=phase,
        passed=not failures,
        failures=tuple(dict.fromkeys(failures)),
        baseline_solved=len(baseline_solved_tasks),
        treatment_solved=len(treatment_solved_tasks),
        losses=losses,
        flips=flips,
        common_solved=common,
        common_solved_deltas=task_deltas,
        task_resource_deltas=task_deltas,
        all_in_resource_deltas=all_in_deltas,
        benchmark_setup_overhead=setup_overhead,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--treatment", type=Path, required=True)
    parser.add_argument(
        "--phase", choices=("preservation", "promotion"), required=True
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    treatment = json.loads(args.treatment.read_text(encoding="utf-8"))
    report = assess_deepswe_release(baseline, treatment, phase=args.phase)
    payload = json.dumps(report.as_dict(), indent=2) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
