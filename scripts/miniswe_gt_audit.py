#!/usr/bin/env python3
"""Validate the frozen DeepSeek baseline and audit a ten-task GT manifest."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make direct ``python scripts/miniswe_gt_audit.py`` invocation behave like the
# existing repository audit CLI without requiring an editable install.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from gt_engine.miniswe_audit import (  # noqa: E402
    audit_attribution,
    load_baseline,
    select_tasks,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--tasks", required=True, type=Path,
                        help="newline-delimited immutable ten-task selection")
    parser.add_argument("--attribution", type=Path,
                        help="JSON array of provider-bound feature rows")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    try:
        baseline = load_baseline(args.baseline)
        task_ids = [line.strip() for line in args.tasks.read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.lstrip().startswith("#")]
        tasks = select_tasks(baseline.results, task_ids)
        report: dict = {
            "baseline": {
                "root": str(baseline.root),
                "model": baseline.model,
                "resolved_floor": baseline.resolved_floor,
                "total_tasks": baseline.total_tasks,
            },
            "tasks": tasks,
            "attribution": None,
        }
        if args.attribution:
            rows = json.loads(args.attribution.read_text(encoding="utf-8"))
            audit = audit_attribution(rows)
            report["attribution"] = {"ok": audit.ok, "issues": list(audit.issues)}
            if not audit.ok:
                raise ValueError("attribution audit failed: " + "; ".join(audit.issues))
        if args.json:
            args.json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"RED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
