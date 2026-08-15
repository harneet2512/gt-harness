#!/usr/bin/env python3
"""Receipt-ladder audit (L1-L4) for a Mini-SWE GT-on run.

Vendored doctrine (gt_math): fired is not delivered; delivered is not consumed.
For every L1 (delivered) receipt the seam recorded, promote L2 (referenced),
L3 (acted), and L4 (resolved_state) by reading the agent's OWN trajectory
chronologically, and run the both-sides dose check (every seal must have an
agent-side observation block).

Usage: python scripts/miniswe_gt_receipt.py <run_dir>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gt_engine.miniswe_receipt import (  # noqa: E402
    both_sides_dose_check,
    load_receipts,
    promote_receipts,
)


def _events(task_dir: Path) -> list[dict]:
    rows: list[dict] = []
    for sub in (task_dir / "agent" / "gt-state").rglob("events.jsonl"):
        for line in sub.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir)
    print(f"=== receipt ladder: {run_dir.name} ===")
    total_ladder: dict[str, dict[str, int]] = {}
    total_dose_ok = True
    for task_dir in sorted(run_dir.iterdir()):
        if not task_dir.is_dir():
            continue
        traj_file = task_dir / "agent" / "miniswe_trajectory.json"
        if not traj_file.exists():
            continue
        raw_traj = json.loads(
            traj_file.read_text(encoding="utf-8", errors="replace")
        )
        msgs = raw_traj.get("messages", [])
        receipts = load_receipts(_events(task_dir))
        if not receipts:
            continue
        terminal_finished = False
        for row in _events(task_dir):
            if row.get("event") == "final_state" and row.get("phase") == "FINISHED":
                terminal_finished = True
        promoted, census = promote_receipts(
            receipts, msgs, terminal_finished=terminal_finished
        )
        dose_ok, dose_issues = both_sides_dose_check(receipts, msgs)
        total_dose_ok = total_dose_ok and dose_ok
        cfg = json.loads((task_dir / "config.json").read_text(encoding="utf-8"))
        name = cfg.get("task", {}).get("path", task_dir.name)
        print(f"\n{name}: delivered={len(receipts)} dose_ok={dose_ok}")
        for ev, entry in sorted(census.items()):
            d, r, a, s = (entry.get(k, 0) for k in
                          ("delivered", "referenced", "acted", "resolved_state"))
            print(f"  {ev:24s} L1={d:2d} L2={r:2d} ({100*r//max(d,1)}%) "
                  f"L3={a:2d} ({100*a//max(d,1)}%) L4={s:2d}")
            agg = total_ladder.setdefault(ev, {"delivered": 0, "referenced": 0,
                                               "acted": 0, "resolved_state": 0})
            for k in agg:
                agg[k] += entry[k]
        if dose_issues:
            print(f"  dose issues: {dose_issues[:3]}")

    print("\n=== AGGREGATE (both-sides ladder) ===")
    for ev, entry in sorted(total_ladder.items()):
        d, r, a, s = (entry[k] for k in
                      ("delivered", "referenced", "acted", "resolved_state"))
        print(f"  {ev:24s} L1={d:2d} L2={r:2d} ({100*r//max(d,1)}%) "
              f"L3={a:2d} ({100*a//max(d,1)}%) L4={s:2d}")
    print(f"dose_reconciliation_ok={total_dose_ok}")
    return 0 if total_dose_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
