"""Evidence-gated Phase II promotion decision; never mutates runtime defaults."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def decide(
    analysis: dict[str, Any] | None,
    offline: dict[str, Any] | None,
    go_receipt: dict[str, Any] | None,
    rollback: dict[str, Any] | None,
) -> dict[str, Any]:
    reasons: list[str] = []
    if (
        not analysis
        or analysis.get("schema") != "gt.phase2.analysis.v1"
        or not analysis.get("paid_run")
        or not analysis.get("execution_receipt_sha256")
        or not analysis.get("matched_identities")
    ):
        reasons.append("authorized_paired_experiment_missing")
    offline_schema = offline.get("schema") if offline else None
    offline_valid = bool(
        offline
        and offline.get("ok")
        and (
            offline_schema == "gt.finalstand.offline_suite.v1"
            or (
                offline_schema == "gt.finalstand.offline_suite.v2"
                and offline.get("terminal") is True
            )
        )
    )
    if not offline_valid:
        reasons.append("offline_validation_missing_or_failed")
    if (
        not go_receipt
        or go_receipt.get("schema") != "gt.go_workflow_receipt.v1"
        or not go_receipt.get("ok")
        or not go_receipt.get("commit_sha")
        or not go_receipt.get("source_sha256")
        or not go_receipt.get("binary_sha256")
    ):
        reasons.append("go_source_binary_receipt_missing_or_failed")
    if (
        not rollback
        or rollback.get("schema") != "gt.rollback_receipt.v1"
        or not rollback.get("ok")
        or not rollback.get("rehearsed")
    ):
        reasons.append("rollback_rehearsal_missing_or_failed")
    eligible: list[str] = []
    if not reasons and analysis:
        for arm, result in analysis.get("comparisons", {}).items():
            solve_ci = result.get("solve_rate_delta_ci95", [0, 0])
            exploration_ci = result.get("exploration_delta_ci95", [0, 0])
            if result.get("paired_tasks", 0) > 0 and solve_ci[0] >= 0 and exploration_ci[1] < 0:
                eligible.append(arm)
        if not eligible:
            reasons.append("no_arm_passes_noninferiority_and_exploration_gate")
    return {
        "schema": "gt.phase2.promotion_decision.v1",
        "promote": bool(eligible) and not reasons,
        "eligible_arms": eligible,
        "reasons": reasons,
        "mutation_performed": False,
    }


def _load(path: Path | None) -> dict[str, Any] | None:
    return json.loads(path.read_text(encoding="utf-8")) if path and path.is_file() else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis", type=Path)
    parser.add_argument("--offline", type=Path)
    parser.add_argument("--go-receipt", type=Path)
    parser.add_argument("--rollback", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = decide(
        _load(args.analysis), _load(args.offline), _load(args.go_receipt), _load(args.rollback)
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["promote"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
