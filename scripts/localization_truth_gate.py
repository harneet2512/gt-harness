#!/usr/bin/env python3
"""Release gate for the committed smoke20 localization truth report.

Fails closed when the report is missing, stale relative to the context
compiler fingerprint, or below any localization quality floor.  This gate is
part of the hosted product certification; a paid benchmark dispatch requires
a passing, fresh report.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.replay_smoke20_localization import _compiler_fingerprint  # noqa: E402

REQUIRED_SCHEMA = "gt.localization_truth_report.v1"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--min-precision", type=float, default=0.7)
    args = parser.parse_args()

    failures: list[str] = []
    if not args.report.exists():
        print(
            f"localization_truth_gate: FAIL missing report {args.report}",
            file=sys.stderr,
        )
        return 1

    data = json.loads(args.report.read_text(encoding="utf-8"))
    summary = data.get("summary") or {}

    if summary.get("schema") != REQUIRED_SCHEMA:
        failures.append(f"schema mismatch: {summary.get('schema')!r}")
    if summary.get("compiler_fingerprint") != _compiler_fingerprint():
        failures.append(
            "stale compiler fingerprint: regenerate the truth report after "
            "compiler changes"
        )
    if summary.get("case_failures"):
        failures.append(f"case failures: {summary['case_failures']}")
    if summary.get("tasks_with_wrong_edit_targets"):
        failures.append(
            "wrong edit targets: "
            f"{summary['tasks_with_wrong_edit_targets']}"
        )
    if summary.get("treatment_failures"):
        failures.append(f"treatment failures: {summary['treatment_failures']}")

    precision = summary.get("mean_edit_target_precision")
    if precision is None:
        failures.append("no audited edit-target precision in report")
    elif float(precision) < args.min_precision:
        failures.append(
            f"precision {precision} below floor {args.min_precision}"
        )

    if failures:
        for failure in failures:
            print(f"localization_truth_gate: FAIL {failure}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "gate": "localization_truth",
                "status": "PASS",
                "cases_run": summary.get("cases_run"),
                "mean_edit_target_precision": precision,
                "zero_target_tasks": len(summary.get("zero_target_tasks") or ()),
                "min_precision_floor": args.min_precision,
                "compiler_fingerprint": _compiler_fingerprint(),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
