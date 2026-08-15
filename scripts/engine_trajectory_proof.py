"""Per-task ENGINE trajectory proof: read artifacts, count engine deliveries,
canonical observations, decision mix, degradation, fallbacks, and reward."""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

DECISION_RE = re.compile(r'decision=\\?"([^"]+)"')
GT_ENGINE_RE = re.compile(r"<result")
FALLBACK_RE = re.compile(r"notice: .*?(fallback|no answer)")


def analyze_task(task_dir: Path) -> dict:
    result: dict = {
        "task": task_dir.name.split("-task-")[-1],
        "trials": 0,
        "reward": None,
        "engine_deliveries": 0,
        "result_blocks": 0,
        "decisions": Counter(),
        "degraded": [],
        "fallbacks": 0,
        "solved": None,
    }
    reward_paths = list(task_dir.rglob("reward.txt"))
    if reward_paths:
        try:
            result["reward"] = float(reward_paths[0].read_text().strip())
        except ValueError:
            result["reward"] = None
    for events_path in task_dir.rglob("events.jsonl"):
        for line in events_path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except Exception:
                continue
            if event.get("event") == "engine_delivery":
                result["engine_deliveries"] += 1
            elif event.get("event") == "gt_degraded_fail_open":
                result["degraded"].append(
                    f"{event.get('stage')}:{event.get('error_type')}"
                )
    for tj in task_dir.rglob("miniswe_trajectory.json"):
        content = tj.read_text(encoding="utf-8")
        result["result_blocks"] += len(GT_ENGINE_RE.findall(content))
        for decision in DECISION_RE.findall(content):
            result["decisions"][decision] += 1
        result["fallbacks"] += len(FALLBACK_RE.findall(content))
    return result


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("root", help="downloaded artifact root")
    args = parser.parse_args()
    root = Path(args.root)
    rows = []
    for task_dir in sorted(root.iterdir()):
        if task_dir.is_dir() and "-task-" in task_dir.name:
            rows.append(analyze_task(task_dir))
    rows.sort(key=lambda r: r["task"])
    print(f"| task | reward | solved | engine_deliveries | result blocks | decisions | degraded | fallbacks |")
    print("|---|---|---|---|---|---|---|---|")
    for row in rows:
        solved = "yes" if row["reward"] and row["reward"] >= 1 else ("no" if row["reward"] is not None else "?")
        decisions = dict(row["decisions"]) or {}
        degraded = ",".join(dict.fromkeys(row["degraded"])) or "-"
        print(
            f"| {row['task']} | {row['reward']} | {solved} | {row['engine_deliveries']} "
            f"| {row['result_blocks']} | {json.dumps(decisions, sort_keys=True)} "
            f"| {degraded} | {row['fallbacks']} |"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
