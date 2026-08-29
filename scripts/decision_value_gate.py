#!/usr/bin/env python3
# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gt_engine.decision_value_corpus import (
    load_decision_value_corpus,
    score_decision_value_observations,
)
from gt_engine.decision_value_gate import evaluate_decision_value_gates


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-runs", type=int, required=True)
    parser.add_argument("--run-receipts", type=Path, nargs="+", required=True)
    parser.add_argument("--fact-checks", type=Path)
    parser.add_argument("--owner-cases", type=Path)
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--observations", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        help="atomically write the benchmark-readiness artifact",
    )
    args = parser.parse_args()
    explicit_rows = args.fact_checks is not None or args.owner_cases is not None
    corpus_rows = args.corpus is not None or args.observations is not None
    if explicit_rows == corpus_rows:
        parser.error(
            "provide either --fact-checks with --owner-cases, or --corpus with "
            "--observations"
        )
    if explicit_rows and (args.fact_checks is None or args.owner_cases is None):
        parser.error("--fact-checks and --owner-cases are required together")
    if corpus_rows and (args.corpus is None or args.observations is None):
        parser.error("--corpus and --observations are required together")
    if corpus_rows:
        scored = score_decision_value_observations(
            load_decision_value_corpus(args.corpus),
            _load(args.observations),
        )
        fact_checks = scored.certified_fact_checks
        owner_cases = scored.implementation_owner_cases
    else:
        fact_checks = _load(args.fact_checks)
        owner_cases = _load(args.owner_cases)
    report = evaluate_decision_value_gates(
        expected_run_count=args.expected_runs,
        run_receipts=(_load(path) for path in args.run_receipts),
        certified_fact_checks=fact_checks,
        implementation_owner_cases=owner_cases,
    )
    report_payload = report.as_dict()
    readiness = {
        "schema": "gt.benchmark_readiness.v1",
        "status": "PASS" if report.passed else "FAIL",
        "benchmark_admissible": bool(report.passed),
        "decision_value": report_payload,
    }
    if args.output is not None:
        _atomic_write_json(args.output, readiness)
    print(json.dumps(report_payload, indent=2, sort_keys=True))
    return 0 if report.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
