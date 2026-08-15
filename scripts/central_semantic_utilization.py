"""Offline semantic utilization audit for archived central-runtime receipts.

This is read-only.  It deliberately reports trajectory alignment, not model
acknowledgment or causal influence.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from gt_engine.preflight import adapt_proposed_action
from gt_engine.trajectory_utilization import SemanticUtilizationTracker


def _task_name(path: Path) -> str:
    for part in path.parts:
        if "-task-" in part:
            return part.rsplit("-task-", 1)[-1]
    return path.parent.name


def audit_task(receipt_path: Path) -> dict[str, Any]:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    cycles = receipt.get("features", {}).get("action_cycles") or []
    calls: dict[int, list[Any]] = {}
    for row in cycles:
        proposed = row.get("proposed") or {}
        call = int(proposed.get("model_call") or 0)
        if not call:
            continue
        action = adapt_proposed_action(
            {"command": proposed.get("raw_command") or "", "tool_call_id": proposed.get("action_id")},
            source_revision=str(proposed.get("source_revision") or ""),
            workspace_revision=str(proposed.get("workspace_revision") or ""),
            model_call=call,
            batch_index=int(proposed.get("batch_index") or 0),
            batch_size=int(proposed.get("batch_size") or 1),
        )
        calls.setdefault(call, []).append(action)

    deliveries: list[dict[str, Any]] = []
    for row in receipt.get("guidance_deliveries") or []:
        row["delivery_kind"] = "feature"
        deliveries.append(row)
    for row in receipt.get("repository_intelligence", {}).get("frontier_deliveries") or []:
        row["delivery_kind"] = "frontier"
        deliveries.append(row)
    deliveries.sort(key=lambda row: int(row.get("delivered_before_call") or row.get("call") or 0))

    tracker = SemanticUtilizationTracker(max_calls=5, max_actions=10)
    for row in deliveries:
        call = int(row.get("delivered_before_call") or row.get("call") or 0)
        source_revision = str(row.get("source_revision") or row.get("revision") or "")
        tracker.register(row, call=call, source_revision=source_revision)
    for call in sorted(calls):
        source_revision = calls[call][0].source_revision
        tracker.observe(call=call, actions=tuple(calls[call]), source_revision=source_revision)
    tracker.finalize()
    summary = tracker.summary()
    return {
        "task": _task_name(receipt_path),
        "deliveries": len(deliveries),
        "summary": summary,
        "rows": deliveries,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_root", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    tasks = [audit_task(path) for path in sorted(args.run_root.rglob("central_receipt.json"))]
    aggregate: dict[str, int] = {}
    for task in tasks:
        for key, value in task["summary"].items():
            aggregate[key] = aggregate.get(key, 0) + int(value)
    result = {"run_root": str(args.run_root), "tasks": tasks, "aggregate": aggregate}
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.json:
        args.json.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
