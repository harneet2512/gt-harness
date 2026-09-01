"""Issue the approval-gated HAR-72 benchmark design without executing it."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

SCHEMA = "gt.har72.benchmark_design.v1"


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def issue(*, root: Path, output: Path) -> dict[str, Any]:
    plan_path = root / "gt_finalstand" / "receipts" / "experiment_execution_plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    tasks = sorted({str(row["task_id"]) for row in plan.get("trials", []) if row.get("task_id")})
    task_manifest_sha = str(plan.get("task_manifest_sha256") or "")
    design: dict[str, Any] = {
        "schema": SCHEMA,
        "status": "BENCHMARK_READY_AWAITING_USER_RUN_APPROVAL",
        "benchmark_ready": False,
        "provider_calls": 0,
        "benchmark_runs": 0,
        "tasks": tasks,
        "task_count": len(tasks),
        "dataset": {"name": "gt_finalstand historical task manifest", "manifest_sha256": task_manifest_sha},
        "parity": {"gt_on": True, "gt_off": True, "paired": True, "control": "fail_closed_gt_off"},
        "model": {"provider": "USER_SELECTION_REQUIRED", "model": "USER_SELECTION_REQUIRED", "configuration": "USER_SELECTION_REQUIRED"},
        "versions": {"miniswe": "installed_dependency_pinned_at_approval", "harbor": "installed_dependency_pinned_at_approval"},
        "limits": {"tasks": len(tasks), "arms": list(plan.get("arms", [])), "trials": int(plan.get("trial_count", 0)), "provider_calls_per_trial": 1},
        "environment": {"source_revision": "approval-time-main", "platform": "approval-time-runner", "seed": "approval-time-explicit"},
        "account_identity_reference": "USER_APPROVAL_RECEIPT_REQUIRED",
        "expected_duration_minutes": None,
        "output_locations": ["gt_finalstand/receipts/har72/", "gt_finalstand/receipts/har72_calibration.json"],
        "estimated_cost_usd": None,
        "hard_cost_ceiling_usd": None,
        "cost_status": "requires_user_model_provider_and_ceiling",
        "proposed_command": "USER_APPROVAL_REQUIRED: run the pinned HAR-72 executor only after approval receipt hashes match",
        "calibration_tables": ["overall", "resolution", "retrieval", "community", "per_mechanism"],
        "approval_requirements": ["design_hash", "task_manifest_sha256", "model_provider_config_hash", "cost_ceiling_usd", "account_identity_reference"],
    }
    design["design_sha256"] = hashlib.sha256(canonical(design)).hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(json.dumps(design, ensure_ascii=False, sort_keys=True, indent=2).encode() + b"\n")
    return design


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    output = args.output or args.root / "gt_finalstand" / "receipts" / "har72_benchmark_design.json"
    print(json.dumps(issue(root=args.root, output=output), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
