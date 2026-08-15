#!/usr/bin/env python3
"""Join official reward, runner, Harbor, and GT outcomes without conflation."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gt_engine.run_outcome import (  # noqa: E402
    ResearchValidity,
    join_trial_outcome,
    summarize_outcomes,
)


def _trial_report(trial_dir: Path) -> dict | None:
    report = trial_dir / "agent" / "miniswe_report.json"
    if not report.exists():
        return None
    try:
        value = json.loads(report.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        return None


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir)
    print(f"=== joined execution outcome: {run_dir.name} ===")
    print(
        f"{'task':34s} {'reward':>6s} {'terminal':>22s} "
        f"{'process':>15s} {'classification':>38s} {'valid':>7s}"
    )
    outcomes = []
    for trial_dir in sorted(run_dir.iterdir()):
        if not trial_dir.is_dir():
            continue
        result_file = trial_dir / "result.json"
        if not result_file.exists():
            continue
        result = json.loads(result_file.read_text(encoding="utf-8"))
        cfg = result.get("config", {})
        task_name = cfg.get("task", {}).get("path", trial_dir.name)
        outcome = join_trial_outcome(
            result, _trial_report(trial_dir), task_name=str(task_name)
        )
        outcomes.append(outcome)
        reward_text = "n/a" if outcome.reward is None else f"{outcome.reward:.2f}"
        print(
            f"{str(task_name):34.34s} {reward_text:>6s} "
            f"{outcome.terminal:>22.22s} {outcome.process_outcome.value:>15.15s} "
            f"{outcome.derived_label:>38.38s} "
            f"{str(outcome.research_validity is ResearchValidity.VALID):>7s}"
        )

    summary = summarize_outcomes(outcomes)
    print()
    for key in (
        "tasks",
        "official_resolved",
        "clean_resolved",
        "clean_submitted_resolved",
        "salvaged_resolved",
        "interrupted_resolved",
        "gt_aborted_resolved",
        "infrastructure_invalid",
        "unclassifiable",
        "runner_reports_present",
    ):
        print(f"{key}={summary[key]}")
    print(f"research_valid={summary['research_valid']}")
    return 0 if summary["research_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
