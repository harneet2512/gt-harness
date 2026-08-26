"""Strict, provider-free comparison of completed benchmark run receipts."""

from __future__ import annotations

import json
import math
from pathlib import Path
from statistics import fmean
from typing import Any

from gt_harness.analysis.delivery import DeliveryAuditError, audit_treatment_delivery
from gt_harness.analysis.uptake import measure_delivery_uptake


class ComparisonError(ValueError):
    """Raised when receipts cannot support a controlled paired comparison."""


def _json_documents(path: Path) -> list[Any]:
    if path.is_dir():
        documents: list[Any] = []
        for candidate in sorted(path.rglob("*.json")):
            try:
                documents.append(json.loads(candidate.read_text(encoding="utf-8")))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
        return documents
    try:
        return [json.loads(path.read_text(encoding="utf-8"))]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ComparisonError(f"cannot read comparison input: {path}") from exc


def load_run_receipts(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path).resolve()
    rows: list[dict[str, Any]] = []
    for document in _json_documents(source):
        candidates: list[Any]
        if isinstance(document, list):
            candidates = document
        elif isinstance(document, dict) and isinstance(document.get("runs"), list):
            candidates = document["runs"]
        else:
            candidates = [document]
        for candidate in candidates:
            if isinstance(candidate, dict) and candidate.get("schema") == "gt.run_receipt.v1":
                rows.append(dict(candidate))
    if not rows:
        raise ComparisonError(f"no gt.run_receipt.v1 documents found: {source}")
    return rows


def _pair_key(receipt: dict[str, Any]) -> str:
    task = str(receipt.get("task_id") or "").strip()
    trial = str(receipt.get("trial_id") or "").strip()
    if not task or not trial:
        raise ComparisonError("every run requires non-empty task_id and trial_id")
    return f"{task}::{trial}"


def _indexed(rows: list[dict[str, Any]], expected_treatment: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        treatment = str(row.get("treatment") or "")
        if treatment != expected_treatment:
            raise ComparisonError(f"expected treatment {expected_treatment!r}, found {treatment!r}")
        if row.get("status") != "COMPLETED":
            raise ComparisonError(f"run {_pair_key(row)} did not complete successfully")
        if not isinstance(row.get("resolved"), bool):
            raise ComparisonError(f"run {_pair_key(row)} has no boolean evaluator outcome")
        evaluation = row.get("evaluation")
        if not isinstance(evaluation, dict) or evaluation.get("schema") != (
            "gt.evaluation_binding.v1"
        ):
            raise ComparisonError(f"run {_pair_key(row)} has no bound evaluator evidence")
        if (
            evaluation.get("resolved") is not row.get("resolved")
            or evaluation.get("task_id") != row.get("task_id")
            or str(evaluation.get("trial_id") or "") != str(row.get("trial_id") or "")
        ):
            raise ComparisonError(f"run {_pair_key(row)} evaluator binding mismatch")
        for field in (
            "run_receipt_sha256",
            "evaluator_receipt_sha256",
            "evaluator_row_sha256",
        ):
            value = str(evaluation.get(field) or "")
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise ComparisonError(f"run {_pair_key(row)} evaluator hash invalid")
        key = _pair_key(row)
        if key in result:
            raise ComparisonError(f"duplicate paired run: {key}")
        result[key] = row
    return result


def _wilson(successes: int, total: int) -> list[float]:
    if total <= 0:
        return [0.0, 0.0]
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1 + z * z / total
    centre = (proportion + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total))
        / denominator
    )
    return [round(max(0.0, centre - margin), 6), round(min(1.0, centre + margin), 6)]


def _exact_discordant_p(treatment_only: int, baseline_only: int) -> float:
    discordant = treatment_only + baseline_only
    if discordant == 0:
        return 1.0
    tail = min(treatment_only, baseline_only)
    probability = sum(math.comb(discordant, value) for value in range(tail + 1)) / (2**discordant)
    return round(min(1.0, 2 * probability), 8)


def _mean(rows: list[dict[str, Any]], field: str) -> float | None:
    values = [float(row[field]) for row in rows if isinstance(row.get(field), (int, float))]
    return round(fmean(values), 6) if values else None


def compare_receipts(
    baseline_rows: list[dict[str, Any]], treatment_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    baseline = _indexed(baseline_rows, "bare")
    treatment = _indexed(treatment_rows, "groundtruth")
    if baseline.keys() != treatment.keys():
        missing_treatment = sorted(baseline.keys() - treatment.keys())
        missing_baseline = sorted(treatment.keys() - baseline.keys())
        raise ComparisonError(
            "paired task mismatch: "
            f"missing_treatment={missing_treatment}; missing_baseline={missing_baseline}"
        )

    pairs = sorted(baseline)
    configuration_mismatches: list[str] = []
    delivery_failures: list[str] = []
    uptake_rows: list[dict[str, Any]] = []
    both = baseline_only = treatment_only = neither = 0
    for key in pairs:
        left = baseline[key]
        right = treatment[key]
        for field in (
            "task_fingerprint",
            "model",
            "base_url_configured",
            "base_url_sha256",
            "temperature",
            "max_iterations",
            "time_budget_seconds",
            "agent_scaffold",
            "system_prompt_sha256",
            "tool_policy_sha256",
            "repository_start",
        ):
            if left.get(field) != right.get(field):
                configuration_mismatches.append(f"{key}:{field}")
        left_solved = bool(left["resolved"])
        right_solved = bool(right["resolved"])
        if left_solved and right_solved:
            both += 1
        elif left_solved:
            baseline_only += 1
        elif right_solved:
            treatment_only += 1
        else:
            neither += 1
        gt = right.get("treatment_receipt")
        if not right.get("treatment_receipt_present") or not isinstance(gt, dict):
            delivery_failures.append(f"{key}:treatment_receipt_missing")
        else:
            try:
                audit = audit_treatment_delivery(
                    gt,
                    initial_context=(
                        str(right.get("initial_context") or "")
                        if "initial_context" in right
                        else None
                    ),
                    repository_end=(
                        right.get("repository_end")
                        if isinstance(right.get("repository_end"), dict)
                        else None
                    ),
                )
            except DeliveryAuditError as exc:
                delivery_failures.append(f"{key}:receipt_invalid:{exc}")
            else:
                delivery_failures.extend(f"{key}:{reason}" for reason in audit["failures"])
                uptake_rows.append(measure_delivery_uptake(right))

    count = len(pairs)
    baseline_solved = both + baseline_only
    treatment_solved = both + treatment_only
    baseline_rate = baseline_solved / count
    treatment_rate = treatment_solved / count
    efficiency_fields = (
        "iterations",
        "provider_calls",
        "input_tokens",
        "output_tokens",
        "cached_tokens",
        "duration_ms",
        "total_cost",
    )
    delta = treatment_rate - baseline_rate
    discordant_p = _exact_discordant_p(treatment_only, baseline_only)
    if delta == 0:
        interpretation = "parity"
    elif discordant_p < 0.05:
        interpretation = (
            "statistically_credible_improvement"
            if delta > 0
            else "statistically_credible_regression"
        )
    else:
        interpretation = "directionally_positive" if delta > 0 else "directionally_negative"
    report = {
        "schema": "gt.paired_comparison.v1",
        "status": (
            "INVALID_EXPERIMENT"
            if configuration_mismatches
            else "INVALID_TREATMENT"
            if delivery_failures
            else "COMPLETE"
        ),
        "sample_size": count,
        "baseline_solved": baseline_solved,
        "treatment_solved": treatment_solved,
        "baseline_solve_rate": round(baseline_rate, 6),
        "treatment_solve_rate": round(treatment_rate, 6),
        "absolute_delta": round(delta, 6),
        "relative_delta": (round(delta / baseline_rate, 6) if baseline_rate else None),
        "baseline_wilson_95": _wilson(baseline_solved, count),
        "treatment_wilson_95": _wilson(treatment_solved, count),
        "pairwise": {
            "both_solve": both,
            "bare_only_solve": baseline_only,
            "groundtruth_only_solve": treatment_only,
            "neither_solve": neither,
            "groundtruth_regressions": baseline_only,
        },
        "discordant_exact_p": discordant_p,
        "interpretation": interpretation,
        "configuration_mismatches": configuration_mismatches,
        "treatment_delivery_failures": delivery_failures,
        "efficiency": {
            field: {
                "bare_mean": _mean(list(baseline.values()), field),
                "groundtruth_mean": _mean(list(treatment.values()), field),
            }
            for field in efficiency_fields
        },
        "uptake": {
            "measurement": "exact_relative_path_in_durable_assistant_action",
            "runs_measured": len(uptake_rows),
            "mean_path_uptake_rate": (
                round(
                    fmean(
                        float(row["path_uptake_rate"])
                        for row in uptake_rows
                        if row.get("path_uptake_rate") is not None
                    ),
                    6,
                )
                if any(row.get("path_uptake_rate") is not None for row in uptake_rows)
                else None
            ),
        },
        "provider_credentials_inspected": False,
        "provider_calls_performed_by_comparison": 0,
    }
    return report


def compare_receipt_paths(baseline: str | Path, treatment: str | Path) -> dict[str, Any]:
    return compare_receipts(load_run_receipts(baseline), load_run_receipts(treatment))


__all__ = [
    "ComparisonError",
    "compare_receipt_paths",
    "compare_receipts",
    "load_run_receipts",
]
