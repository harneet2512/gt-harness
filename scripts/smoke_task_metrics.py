#!/usr/bin/env python3
"""Per-task metrics for a DeepSWE smoke artifact tree.

Reads each task's agent/gt-run.json + gt-state events.jsonl and emits one
row per task: token accounting (input/output/cached), GT-delivered context
bytes, wait/idle decomposition, deliveries, churn, and the honest outcome.

Money is deliberately absent: the OpenRouter route is discounted, so cost
fields are not meaningful evidence. Tokens and time are the honest units.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _parse_ts(value: object) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _usage_tokens(usage: dict[str, Any]) -> tuple[int, int, int]:
    """(input, output, cached) from a usage object."""
    if not isinstance(usage, dict):
        return 0, 0, 0
    cached = usage.get("prompt_tokens_details") or {}
    return (
        int(usage.get("prompt_tokens") or 0),
        int(usage.get("completion_tokens") or 0),
        int(cached.get("cached_tokens") or 0),
    )


def task_metrics(task_dir: Path) -> dict[str, Any]:
    """Metrics for one <task>__<trial>/agent tree. Missing evidence stays
    explicit: absent files produce 'unknown' fields, never zeros."""
    row: dict[str, Any] = {"task": task_dir.name.split("__", 1)[0]}
    agent = task_dir / "agent"
    run_path = agent / "gt-run.json"
    if not run_path.is_file():
        row["status"] = "missing_product_receipt"
        return row

    run = json.loads(run_path.read_text(encoding="utf-8"))
    receipt = run.get("treatment_receipt") or {}
    row["status"] = run.get("status") or receipt.get("status") or "unknown"
    row["verified"] = bool(receipt.get("verified"))
    row["unmet_predicates"] = len(receipt.get("unmet_predicates") or [])
    row["gt_mode"] = receipt.get("gt_mode") or "unknown"

    # Token totals from the product receipt (top level of gt-run.json).
    for key in ("input_tokens", "cached_tokens", "output_tokens",
                "provider_calls", "provider_completed_calls",
                "provider_failed_calls", "delivery_count"):
        value = run.get(key, receipt.get(key))
        row[key] = value if isinstance(value, int) else "unknown"

    # Journal: wait/idle decomposition + delivery bytes.
    events_path = next(agent.glob("gt-state/*/events.jsonl"), None)
    if events_path is None:
        row["journal"] = "missing"
        return row
    events = [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    counts = defaultdict(int)
    for e in events:
        counts[str(e.get("event") or "?")] += 1

    # Provider wait = admission -> response latency summed over calls.
    # Agent think time = response -> next admission. Graph build = elapsed_ms.
    wait_s = 0.0
    gap_s = 0.0
    last_response_ts: float | None = None
    admitted_ts: float | None = None
    delivered_bytes = 0
    refused_bytes = 0
    per_call_in = []
    for e in events:
        ts = _parse_ts(e.get("timestamp_utc"))
        ev = e.get("event")
        if ev == "provider_admission":
            admitted_ts = ts
            if last_response_ts is not None and ts is not None and ts >= last_response_ts:
                gap_s += ts - last_response_ts
        elif ev == "provider_response":
            if admitted_ts is not None and ts is not None and ts >= admitted_ts:
                wait_s += ts - admitted_ts
            last_response_ts = ts
            tin, tout, tcache = _usage_tokens(e.get("usage") or {})
            if tin or tout:
                per_call_in.append(tin)
        elif ev in ("delivery_prepared", "evidence_delivery",
                    "context_addition_delivery"):
            delivered_bytes += int(e.get("rendered_bytes") or 0)
        elif ev == "delivery_refused":
            refused_bytes += int(e.get("rendered_bytes") or 0)

    row["provider_wait_seconds"] = round(wait_s, 1)
    row["agent_gap_seconds"] = round(gap_s, 1)
    row["graph_build_ms_total"] = sum(
        int(e.get("elapsed_ms") or 0) for e in events
        if e.get("event") == "graph_build_mode"
    )
    row["gt_delivered_bytes"] = delivered_bytes
    row["gt_refused_bytes"] = refused_bytes
    row["deliveries_consumed"] = counts.get("delivery_consumed", "unknown")
    row["churn_events"] = sum(
        v for k, v in counts.items() if k.startswith("churn"))
    row["submit_refusals"] = counts.get("submit_refusal", 0)
    row["journal_events"] = len(events)
    row["integrity_issues"] = None  # filled by caller from audit when present
    if per_call_in:
        row["tokens_per_call_mean"] = round(
            sum(per_call_in) / len(per_call_in), 1)
        row["tokens_per_call_max"] = max(per_call_in)
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path,
                        help="downloaded artifact tree (contains tasks/)")
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    tasks_root = args.root / "tasks"
    if not tasks_root.is_dir():
        tasks_root = args.root
    task_dirs = sorted(
        p for p in tasks_root.rglob("agent") if p.is_dir()
    )
    rows = [task_metrics(d.parent) for d in task_dirs]
    out = json.dumps(rows, indent=2, sort_keys=True)
    if args.json:
        args.json.write_text(out + "\n", encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
