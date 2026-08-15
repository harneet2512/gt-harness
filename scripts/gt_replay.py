"""Write exact per-iteration replay reports for one run artifact tree."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from gt_engine.attribution import verify_trace_rows
from gt_engine.replay import build_iteration_replay


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    tasks: dict[str, object] = {}
    issues: list[str] = []
    for path in sorted(args.run_dir.glob("*/agent/gt_attribution.jsonl")):
        rows = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        task = path.parent.parent.name
        trace_issues = verify_trace_rows(rows)
        replay = build_iteration_replay(rows)
        tasks[task] = replay
        issues.extend(f"{task}: {item}" for item in trace_issues)
        issues.extend(f"{task}: {item}" for item in replay["issues"])
    report = {
        "version": "gt.run_replay.v1",
        "task_count": len(tasks),
        "tasks": tasks,
        "issues": issues,
    }
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.json:
        args.json.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 1 if issues or not tasks else 0


if __name__ == "__main__":
    raise SystemExit(main())
