"""Matched-cohort comparison: GT-on vs GT-off on the same tasks.

Joins three frozen inputs by ``task_name`` (never row position):

* GT-off per-trial slice (``freeze_deepswe_gtoff_trials.py`` output, or any
  ``gt.deepswe_gtoff_trials.v1`` / ``gt.deepswe_muse_baseline.v1`` payload).
* The cohort manifest (``eval/deepswe_smoke20_v1.json``) naming the tasks.
* A GT-on run tree: every ``**/official-verifier-result.json`` pairs with the
  sibling ``agent/gt-run.json`` usage totals.

Token efficiency is reported in tokens - never dollars. Cache composition is
reported separately from input+output totals because provider semantics
differ (DeepSeek ``prompt_cache_hit_tokens`` vs trial ``n_cache_tokens``).

Missing tasks, ungraded results and absent usage stay typed (``null`` /
``incomplete``) - they are never coerced to 0 or skipped silently.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from gt_harness.canonical_io import atomic_json

_GTOFF_SCHEMAS = {"gt.deepswe_gtoff_trials.v1", "gt.deepswe_muse_baseline.v1"}


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _gtoff_task_stats(trials: list[dict[str, Any]]) -> dict[str, Any]:
    included = [t for t in trials if t.get("included_in_score", True)]
    graded = [t for t in included if t.get("passed") is not None]
    passed = [t for t in graded if t.get("passed")]
    totals = [
        (t.get("n_input_tokens") or 0) + (t.get("n_output_tokens") or 0)
        for t in graded
        if isinstance(t.get("n_input_tokens"), (int, float))
        and isinstance(t.get("n_output_tokens"), (int, float))
    ]
    return {
        "n_trials": len(trials),
        "n_graded": len(graded),
        "n_passed": len(passed),
        "pass_rate": (len(passed) / len(graded)) if graded else None,
        "mean_input_tokens": _mean([t["n_input_tokens"] for t in graded
                                    if isinstance(t.get("n_input_tokens"), (int, float))]),
        "mean_output_tokens": _mean([t["n_output_tokens"] for t in graded
                                     if isinstance(t.get("n_output_tokens"), (int, float))]),
        "mean_total_tokens": _mean(totals),
        "mean_cache_tokens": _mean([t["n_cache_tokens"] for t in graded
                                    if isinstance(t.get("n_cache_tokens"), (int, float))]),
        "mean_peak_context_tokens": _mean([t["peak_context_tokens"] for t in graded
                                           if isinstance(t.get("peak_context_tokens"), (int, float))]),
        "mean_agent_steps": _mean([t["n_agent_steps"] for t in graded
                                   if isinstance(t.get("n_agent_steps"), (int, float))]),
    }


def _gtoff_by_task(baseline: dict[str, Any]) -> dict[str, dict[str, Any]]:
    schema = baseline.get("schema")
    if schema not in _GTOFF_SCHEMAS:
        raise ValueError(f"unsupported baseline schema: {schema}")
    if schema == "gt.deepswe_gtoff_trials.v1":
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in baseline["trials"]:
            grouped.setdefault(str(row["task_name"]), []).append(row)
        return {task: _gtoff_task_stats(rows) for task, rows in grouped.items()}
    # muse slice: per-task rows already carry a trials array
    return {
        str(row["task_name"]): _gtoff_task_stats(row.get("trials", []))
        for row in baseline.get("tasks", [])
    }


def _gton_rows(root: Path) -> dict[str, dict[str, Any]]:
    """task_id -> {solved, usage...} from verifier receipts + run receipts."""
    rows: dict[str, dict[str, Any]] = {}
    for receipt_path in sorted(root.rglob("official-verifier-result.json")):
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        task_id = str(receipt.get("task_id") or "")
        if not task_id:
            continue
        row: dict[str, Any] = {
            "solved": receipt.get("solved"),
            "status": receipt.get("status"),
            "failure_class": receipt.get("failure_class"),
        }
        run_path = receipt_path.parent / "gt-run.json"
        if run_path.exists():
            try:
                run = json.loads(run_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                run = {}
            usage = run.get("usage") or {}
            prompt = usage.get("prompt_tokens")
            completion = usage.get("completion_tokens")
            row["usage"] = {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "cache_hit_tokens": usage.get("prompt_cache_hit_tokens"),
                "cache_miss_tokens": usage.get("prompt_cache_miss_tokens"),
                "total_tokens": (
                    (prompt or 0) + (completion or 0)
                    if isinstance(prompt, (int, float))
                    or isinstance(completion, (int, float))
                    else None
                ),
            }
        else:
            row["usage"] = None
        rows[task_id] = row
    return rows


def compare(
    *, baseline: dict[str, Any], task_ids: list[str], gton_root: Path,
) -> dict[str, Any]:
    gtoff = _gtoff_by_task(baseline)
    gton = _gton_rows(gton_root)
    missing_gtoff = [t for t in task_ids if t not in gtoff]
    missing_gton = [t for t in task_ids if t not in gton]

    tasks: list[dict[str, Any]] = []
    for task_id in task_ids:
        off = gtoff.get(task_id)
        on = gton.get(task_id)
        off_total = (off or {}).get("mean_total_tokens")
        on_usage = (on or {}).get("usage") or {}
        on_total = on_usage.get("total_tokens")
        tasks.append({
            "task_name": task_id,
            "gtoff": off,
            "gton": {
                "solved": (on or {}).get("solved"),
                "status": (on or {}).get("status"),
                "failure_class": (on or {}).get("failure_class"),
                "usage": on_usage or None,
            },
            "delta_total_tokens": (
                on_total - off_total
                if isinstance(on_total, (int, float))
                and isinstance(off_total, (int, float))
                else None
            ),
        })

    off_rates = [t["gtoff"]["pass_rate"] for t in tasks
                 if t["gtoff"] and t["gtoff"]["pass_rate"] is not None]
    off_any = [t for t in tasks if t["gtoff"] and t["gtoff"]["n_passed"]]
    on_graded = [t for t in tasks
                 if t["gton"]["solved"] is not None]
    on_solved = [t for t in on_graded if t["gton"]["solved"]]
    on_totals = [t["gton"]["usage"]["total_tokens"] for t in tasks
                 if t["gton"]["usage"]
                 and isinstance(t["gton"]["usage"].get("total_tokens"), (int, float))]
    off_totals = [t["gtoff"]["mean_total_tokens"] for t in tasks
                  if t["gtoff"] and t["gtoff"]["mean_total_tokens"] is not None]
    paired_delta = [t["delta_total_tokens"] for t in tasks
                    if t["delta_total_tokens"] is not None]

    return {
        "schema": "gt.matched_cohort_comparison.v1",
        "join_key": "task_name",
        "cohort_size": len(task_ids),
        "conservation": {
            "complete": not missing_gtoff and not missing_gton,
            "missing_gtoff_tasks": missing_gtoff,
            "missing_gton_tasks": missing_gton,
            "gton_ungraded_tasks": [
                t["task_name"] for t in tasks if t["gton"]["solved"] is None
            ],
        },
        "cohort": {
            "gtoff": {
                "pass_at_1": _mean(off_rates),
                "pass_at_k": (len(off_any) / len(task_ids)) if task_ids else None,
                "tasks_with_any_pass": len(off_any),
                "mean_total_tokens": _mean(off_totals),
                "n_tasks_with_stats": len(off_rates),
            },
            "gton": {
                "solve_rate": (len(on_solved) / len(on_graded)) if on_graded else None,
                "n_graded": len(on_graded),
                "n_solved": len(on_solved),
                "mean_total_tokens": _mean(on_totals),
                "n_tasks_with_usage": len(on_totals),
            },
            "paired": {
                "n_pairs_with_token_delta": len(paired_delta),
                "mean_delta_total_tokens": _mean(paired_delta),
                "per_task_total_tokens": {
                    t["task_name"]: {
                        "gtoff_mean": (t["gtoff"] or {}).get("mean_total_tokens"),
                        "gton": ((t["gton"]["usage"] or {}).get("total_tokens")
                                 if t["gton"]["usage"] else None),
                    }
                    for t in tasks
                },
            },
        },
        "tasks": tasks,
    }


def _render_text(result: dict[str, Any]) -> str:
    cohort = result["cohort"]
    cons = result["conservation"]
    off, on, paired = cohort["gtoff"], cohort["gton"], cohort["paired"]

    def fmt(value, digits=4):
        return "unknown" if value is None else (
            f"{value:.{digits}f}" if isinstance(value, float) else str(value))

    lines = [
        f"matched cohort: {result['cohort_size']} tasks "
        f"(complete: {cons['complete']})",
        f"GT-off  pass@1 {fmt(off['pass_at_1'])}  pass@k {fmt(off['pass_at_k'])}"
        f"  mean_total_tokens {fmt(off['mean_total_tokens'], 0)}",
        f"GT-on   solve   {fmt(on['solve_rate'])} ({on['n_solved']}/{on['n_graded']} graded)"
        f"  mean_total_tokens {fmt(on['mean_total_tokens'], 0)}"
        f"  (usage on {on['n_tasks_with_usage']} tasks)",
        f"paired  mean delta total tokens {fmt(paired['mean_delta_total_tokens'], 0)}"
        f"  on {paired['n_pairs_with_token_delta']} pairs",
    ]
    if cons["missing_gtoff_tasks"]:
        lines.append(f"missing GT-off: {cons['missing_gtoff_tasks']}")
    if cons["missing_gton_tasks"]:
        lines.append(f"missing GT-on: {cons['missing_gton_tasks']}")
    if cons["gton_ungraded_tasks"]:
        lines.append(f"GT-on ungraded: {cons['gton_ungraded_tasks']}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True,
                        help="frozen GT-off slice (gt.deepswe_gtoff_trials.v1 or muse baseline)")
    parser.add_argument("--manifest", type=Path, required=True,
                        help="cohort manifest with task_ids (e.g. eval/deepswe_smoke20_v1.json)")
    parser.add_argument("--gton-root", type=Path, required=True,
                        help="GT-on run tree containing official-verifier-result.json receipts")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    task_ids = [str(t) for t in manifest["task_ids"]]
    result = compare(baseline=baseline, task_ids=task_ids, gton_root=args.gton_root)
    if args.output:
        atomic_json(args.output, result)
    print(_render_text(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
