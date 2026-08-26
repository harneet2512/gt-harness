#!/usr/bin/env python3
"""Fail-closed release gate for a role-aware localization truth report."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.replay_smoke20_localization import (  # noqa: E402
    REPORT_SCHEMA,
    _compiler_fingerprint,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--oracle", type=Path, default=None)
    parser.add_argument("--min-exact-precision", type=float, default=0.95)
    parser.add_argument("--min-required-coverage", type=float, default=0.85)
    parser.add_argument("--min-ambiguity-recall", type=float, default=0.90)
    parser.add_argument("--min-implementation-precision", type=float, default=0.50)
    args = parser.parse_args()

    if not args.report.exists():
        print(f"localization_truth_gate: FAIL missing report {args.report}", file=sys.stderr)
        return 1
    data = json.loads(args.report.read_text(encoding="utf-8"))
    summary = data.get("summary") or {}
    failures: list[str] = []

    if summary.get("schema") != REPORT_SCHEMA:
        failures.append(f"schema mismatch: {summary.get('schema')!r}")
    if summary.get("compiler_fingerprint") != _compiler_fingerprint():
        failures.append("stale compiler fingerprint")
    if args.manifest is not None and summary.get("manifest_sha256") != _sha256(args.manifest):
        failures.append("stale manifest fingerprint")
    if args.oracle is not None and summary.get("oracle_sha256") != _sha256(args.oracle):
        failures.append("stale oracle fingerprint")
    if summary.get("retrieval_mode") != "hybrid_required":
        failures.append("report was not generated in hybrid_required mode")
    if int(summary.get("cases_run", -1)) != int(summary.get("cases_expected", -2)):
        failures.append(
            f"task-set incomplete: expected {summary.get('cases_expected')}, "
            f"ran {summary.get('cases_run')}"
        )
    for key in (
        "case_failures",
        "missing_oracle_tasks",
        "extra_oracle_tasks",
        "tasks_with_false_edit_authority",
        "tasks_below_half_required_coverage",
        "treatment_failures",
        "dense_not_ready_tasks",
    ):
        if summary.get(key):
            failures.append(f"{key}: {summary[key]}")

    precision = summary.get("mean_exact_edit_precision")
    coverage = summary.get("mean_required_facet_coverage")
    ambiguity = summary.get("mean_ambiguity_candidate_recall")
    implementation_precision = summary.get("implementation_role_precision")
    if precision is None or float(precision) < args.min_exact_precision:
        failures.append(f"exact edit precision {precision} below floor {args.min_exact_precision}")
    if coverage is None or float(coverage) < args.min_required_coverage:
        failures.append(
            f"required facet coverage {coverage} below floor {args.min_required_coverage}"
        )
    if ambiguity is not None and float(ambiguity) < args.min_ambiguity_recall:
        failures.append(
            f"ambiguity candidate recall {ambiguity} below floor {args.min_ambiguity_recall}"
        )
    if (
        implementation_precision is None
        or float(implementation_precision) < args.min_implementation_precision
    ):
        failures.append(
            "implementation role precision "
            f"{implementation_precision} below floor {args.min_implementation_precision}"
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
                "mean_exact_edit_precision": precision,
                "mean_required_facet_coverage": coverage,
                "mean_ambiguity_candidate_recall": ambiguity,
                "implementation_role_precision": implementation_precision,
                "compiler_fingerprint": _compiler_fingerprint(),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
