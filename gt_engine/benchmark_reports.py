"""Separated, machine-readable release reports for matched GT benchmarks."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any


def _row_map(payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("task") or ""): dict(row)
        for row in payload.get("rows") or ()
        if isinstance(row, dict) and str(row.get("task") or "")
    }


def build_benchmark_reports(
    *,
    expected_tasks: Iterable[str],
    baseline: Mapping[str, Any],
    treatment: Mapping[str, Any],
    receipt_metrics: Iterable[Mapping[str, Any]],
    integrity_failures: Iterable[str],
    efficiency: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Build four reports without conflating integrity with model outcomes."""

    expected = tuple(dict.fromkeys(str(task) for task in expected_tasks if str(task)))
    expected_set = set(expected)
    baseline_rows = _row_map(baseline)
    treatment_rows = _row_map(treatment)
    metric_rows = [dict(row) for row in receipt_metrics]
    metric_tasks = [str(row.get("task") or "") for row in metric_rows]
    failures = list(dict.fromkeys(str(item) for item in integrity_failures if str(item)))
    complete_task_set = bool(
        set(treatment_rows) == expected_set
        and set(metric_tasks) == expected_set
        and len(metric_tasks) == len(set(metric_tasks)) == len(expected)
    )
    if not complete_task_set:
        failures.append("matched_task_set_incomplete_or_duplicated")
    efficiency_valid = bool(
        complete_task_set
        and not any("receipt" in failure.lower() for failure in failures)
        and all(row.get("receipt_complete", True) is True for row in metric_rows)
    )

    categories = {
        "both_solve": [],
        "baseline_only": [],
        "gt_only": [],
        "both_fail": [],
    }
    for task in expected:
        baseline_solved = baseline_rows.get(task, {}).get("solved") is True
        treatment_solved = treatment_rows.get(task, {}).get("solved") is True
        if baseline_solved and treatment_solved:
            categories["both_solve"].append(task)
        elif baseline_solved:
            categories["baseline_only"].append(task)
        elif treatment_solved:
            categories["gt_only"].append(task)
        else:
            categories["both_fail"].append(task)

    exact_operations = [
        {
            "task": str(row.get("task") or ""),
            "provider_calls": row.get("api_calls"),
            "model_actions": row.get("actions"),
            "retrieval_computations": row.get(
                "preemptive_retrieval_shared_computations"
            ),
            "provider_deliveries": row.get("provider_delivery_count"),
            "gt_visible_chars": row.get("provider_delivery_visible_chars"),
        }
        for row in sorted(metric_rows, key=lambda item: str(item.get("task") or ""))
    ]
    surfaces: Counter[str] = Counter()
    uptake: Counter[str] = Counter()
    for row in metric_rows:
        surfaces.update(
            {
                str(key): int(value or 0)
                for key, value in (row.get("intervention_surface_counts") or {}).items()
            }
        )
        uptake.update(
            {
                str(key): int(value or 0)
                for key, value in (row.get("behavioral_uptake") or {}).items()
            }
        )
    return {
        "integrity": {
            "schema": "gt.benchmark_integrity_report.v1",
            "passed": complete_task_set and not failures,
            "expected_task_count": len(expected),
            "complete_task_set": complete_task_set,
            "failures": list(dict.fromkeys(failures)),
        },
        "solve": {
            "schema": "gt.benchmark_solve_report.v1",
            "categories": categories,
            "baseline_solved": len(categories["both_solve"])
            + len(categories["baseline_only"]),
            "gt_solved": len(categories["both_solve"]) + len(categories["gt_only"]),
            "causal_claim_policy": "counterfactual_required",
        },
        "efficiency": {
            "schema": "gt.benchmark_efficiency_report.v1",
            "valid": efficiency_valid,
            "invalid_reason": (
                "missing_or_invalid_run_receipt" if not efficiency_valid else ""
            ),
            "aggregate": dict(efficiency) if efficiency_valid else None,
            # Compatibility projection for complete historical inputs.  When
            # invalid, no aggregate values are exposed as if missing usage
            # were zero.
            **(dict(efficiency) if efficiency_valid else {}),
            "exact_operations": exact_operations if efficiency_valid else [],
        },
        "intervention": {
            "schema": "gt.benchmark_intervention_report.v1",
            "surface_counts": dict(sorted(surfaces.items())),
            "behavioral_uptake": dict(sorted(uptake.items())),
            "chain_rows": sum(
                int(row.get("intervention_chain_rows") or 0) for row in metric_rows
            ),
            "causal_status": "UNIDENTIFIABLE_WITHOUT_COUNTERFACTUAL",
        },
    }


__all__ = ["build_benchmark_reports"]
