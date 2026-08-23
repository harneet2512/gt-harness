#!/usr/bin/env python3
"""Merge-independent TB2 task/outcome and resource snapshot."""
from __future__ import annotations
import json, os
from pathlib import Path

def main() -> int:
    root = Path(os.environ.get("ARTIFACT_ROOT", "tasks"))
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    prefix = f"deepswe-central-{run_id}-" if run_id else "deepswe-central-"
    rows = []
    for task_root in sorted(root.glob("deepswe-central-*-*")):
        task = task_root.name.removeprefix(prefix)
        results = [json.loads(p.read_text(encoding="utf-8")) for p in task_root.rglob("result.json")]
        result = next((r for r in results if "verifier_result" in r or "exception_info" in r), {})
        verifier = result.get("verifier_result") if isinstance(result, dict) else {}
        verifier = verifier if isinstance(verifier, dict) else {}
        rewards = verifier.get("rewards")
        rewards = rewards if isinstance(rewards, dict) else {}
        reward = rewards.get("reward")
        reward = float(reward) if isinstance(reward, (int, float)) and not isinstance(reward, bool) and reward in (0, 1) else None
        rows.append({"task": task, "reward": reward,
                     "exception": bool(result.get("exception_info")) if isinstance(result, dict) else False,
                     "receipt": bool(list(task_root.rglob("central_receipt.json")))})
    graded = [r for r in rows if r["reward"] is not None]
    solved = [r for r in graded if r["reward"] == 1.0]
    payload = {"schema": "gt.tb2.partial.v1", "run_id": os.environ.get("GITHUB_RUN_ID"),
               "returned": len(rows), "graded": len(graded), "ungraded": len(rows)-len(graded),
               "solved": len(solved), "unsolved": len(graded)-len(solved),
               "rows": rows}
    out = Path(os.environ.get("OUTPUT", "tb2_partial_summary.json"))
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print("## TB2 partial results")
    print(f"returned={payload['returned']} graded={payload['graded']} solved={payload['solved']} unsolved={payload['unsolved']} ungraded={payload['ungraded']}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
