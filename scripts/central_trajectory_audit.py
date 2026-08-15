"""Fail-closed trajectory audit for archived GT-on runs.

This audit proves what can be proved from a trajectory and its central receipt:
grounding, first-eligible delivery, source-revision/accounting integrity, and
the deterministic controller disposition of every effect.  It deliberately
does *not* infer model causality from anchor-following.  A model-level causal
claim requires a replayable provider request, model state, and a counterfactual
run; archived receipts normally do not contain those artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gt_engine.delivery_audit import audit_provider_deliveries  # noqa: E402

KNOWN_DISPOSITIONS = {
    "provider_payload",
    "existing_engine_actuation",
    "engine_internal_state",
    "audit_only",
    "unread_private_state",
}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _task_name(path: Path) -> str:
    result_path = path.parent.parent / "result.json"
    if result_path.exists():
        try:
            task_name = str(_load(result_path).get("task_name") or "").strip()
        except (OSError, ValueError, json.JSONDecodeError):
            task_name = ""
        if task_name:
            return task_name.rsplit("/", 1)[-1]
    for parent in path.parents:
        if "-task-" in parent.name:
            return parent.name.split("-task-", 1)[1]
        if "__" in parent.name:
            candidate = parent.name.split("__", 1)[0].strip()
            if candidate:
                return candidate
    return path.parent.name


def _discover(root: Path) -> dict[str, tuple[Path, Path]]:
    pairs: dict[str, tuple[Path, Path]] = {}
    for trajectory in sorted(root.rglob("miniswe_trajectory.json")):
        receipt = trajectory.parent / "central_receipt.json"
        if not receipt.exists():
            continue
        task = _task_name(trajectory)
        pair = (trajectory, receipt)
        if task in pairs:
            if tuple(_sha256(p) for p in pairs[task]) != tuple(_sha256(p) for p in pair):
                raise ValueError(f"conflicting trajectory/receipt pair for {task!r}")
            continue
        pairs[task] = pair
    if not pairs:
        raise ValueError(f"no trajectory/receipt pairs below {root}")
    return pairs


def _trajectory_action_count(trajectory: dict[str, Any]) -> int:
    return sum(
        len((message.get("extra") or {}).get("actions") or [])
        for message in trajectory.get("messages") or []
        if isinstance(message, dict) and message.get("role") == "assistant"
    )


def _has_anchor(row: dict[str, Any]) -> bool:
    """Return true only when a delivery names concrete source-backed evidence."""
    for key in ("claim_anchors", "fact_ids", "claim_ids"):
        if any(str(item).strip() for item in (row.get(key) or ())):
            return True
    opportunity = row.get("certified_opportunity") or {}
    if any(str(item).strip() for item in opportunity.get("concrete_anchors") or ()):
        return True
    for fact in row.get("facts") or ():
        if not isinstance(fact, dict):
            continue
        if any(str(fact.get(key) or "").strip() for key in ("path", "symbol", "value")):
            return True
    return False


def _replay_state_available(receipt: dict[str, Any]) -> bool:
    """Require explicit artifacts; hashes alone are not replay state."""
    state = receipt.get("replay_state") or receipt.get("model_replay") or {}
    if not isinstance(state, dict):
        return False
    if state.get("model_causal_replay_ready") is True:
        return True
    required = ("provider_request_bodies", "model_state", "sampling_state")
    return all(state.get(key) for key in required)


def _context_failures(receipt: dict[str, Any], task: str) -> list[str]:
    failures: list[str] = []
    contexts = receipt.get("model_call_contexts") or []
    if not contexts:
        return [f"{task}:missing_model_call_contexts"]
    seen_calls: set[int] = set()
    for row in contexts:
        call = row.get("call")
        if not isinstance(call, int) or call < 1 or call in seen_calls:
            failures.append(f"{task}:duplicate_or_invalid_context_call:{call}")
        seen_calls.add(call)
        if not str(row.get("request_payload_sha256") or ""):
            failures.append(f"{task}:missing_provider_request_hash:call:{call}")
        if not str(row.get("provider_messages_sha256") or ""):
            failures.append(f"{task}:missing_provider_messages_hash:call:{call}")
        candidates = row.get("context_fact_candidates")
        accounted = row.get("context_facts_accounted")
        if candidates is None or accounted is None:
            failures.append(f"{task}:missing_context_fact_accounting:call:{call}")
        elif candidates != accounted:
            failures.append(f"{task}:context_fact_accounting_mismatch:call:{call}")
    return failures


def _effect_report(receipt: dict[str, Any], task: str) -> tuple[list[dict[str, Any]], list[str]]:
    effects = (receipt.get("features") or {}).get("effect_trace") or []
    failures: list[str] = []
    ids: set[str] = set()
    rows: list[dict[str, Any]] = []
    for effect in effects:
        effect_id = str(effect.get("effect_id") or "")
        disposition = str(effect.get("disposition") or "")
        if not effect_id or effect_id in ids:
            failures.append(f"{task}:duplicate_or_missing_effect_id:{effect_id or '<missing>'}")
        ids.add(effect_id)
        if disposition not in KNOWN_DISPOSITIONS:
            failures.append(f"{task}:unknown_effect_disposition:{disposition or '<missing>'}")
        timing = effect.get("timing") or {}
        if timing.get("late") is True or timing.get("predictive") is True:
            failures.append(f"{task}:effect_timing_violation:{effect_id}")
        rows.append(
            {
                "effect_id": effect_id,
                "feature_id": effect.get("feature_id"),
                "evidence_action": effect.get("evidence_action"),
                "disposition": disposition,
                "provider_delivery_ids": list(effect.get("provider_delivery_ids") or []),
                "controller_actuation": disposition == "existing_engine_actuation"
                or bool(effect.get("actuator_events")),
            }
        )
    return rows, failures


def _delivery_report(receipt: dict[str, Any], task: str) -> tuple[list[dict[str, Any]], list[str]]:
    all_rows, failures, _totals = audit_provider_deliveries(receipt, task=task)
    rows: list[dict[str, Any]] = []
    for row in all_rows:
        raw = row["raw"]
        feature = str(row.get("feature_id") or "repository_frontier")
        if not row["claim_count"] and _has_anchor(raw):
            # Older frontier records used concrete facts without claim IDs.
            # Preserve the historical concrete-anchor audit while the unified
            # stream still reports the missing claim as a contract failure.
            concrete = True
        else:
            concrete = bool(row["claim_count"])
        if not concrete:
            failures.append(
                f"{task}:delivery_without_concrete_anchor:{row['surface_index']}:{feature}"
            )
        rows.append(
            {
                "surface": row["surface"],
                "feature_id": feature,
                "evidence_action": row["evidence_action"],
                "first_eligible_call": row["first_eligible_call"],
                "delivered_before_call": row["delivered_before_call"],
                "deterministic_status": (
                    "VALID" if concrete and row["deterministic_status"] == "VALID" else "INVALID"
                ),
                "causal_status": "UNIDENTIFIABLE_NO_REPLAY_STATE",
                "semantic_utilization": str(raw.get("semantic_utilization") or "unreported"),
                "anchor_followed": raw.get("anchor_followed"),
                "chars": row["chars"],
                "claim_count": row["claim_count"],
            }
        )
    return rows, failures


def audit_task(trajectory_path: Path, receipt_path: Path, task: str) -> dict[str, Any]:
    trajectory = _load(trajectory_path)
    receipt = _load(receipt_path)
    failures = _context_failures(receipt, task)
    effects, effect_failures = _effect_report(receipt, task)
    deliveries, delivery_failures = _delivery_report(receipt, task)
    _all_delivery_rows, _all_delivery_failures, delivery_totals = audit_provider_deliveries(
        receipt, task=task
    )
    failures.extend(effect_failures)
    failures.extend(delivery_failures)
    action_count = _trajectory_action_count(trajectory)
    recorded_actions = receipt.get("actions")
    if isinstance(recorded_actions, int) and recorded_actions != action_count:
        failures.append(
            f"{task}:trajectory_receipt_action_count_mismatch:{action_count}!={recorded_actions}"
        )
    replay_available = _replay_state_available(receipt)
    if replay_available:
        for row in deliveries:
            row["causal_status"] = "REPLAY_STATE_AVAILABLE_COUNTERFACTUAL_REQUIRED"
    dispositions = Counter(row["disposition"] for row in effects)
    return {
        "task": task,
        "trajectory_actions": action_count,
        "receipt_actions": recorded_actions,
        "effects": effects,
        "effect_dispositions": dict(sorted(dispositions.items())),
        "deliveries": deliveries,
        "provider_delivery_totals": delivery_totals,
        "replay_state_available": replay_available,
        "failures": failures,
    }


def audit_run_root(root: Path) -> dict[str, Any]:
    pairs = _discover(root.resolve())
    tasks: dict[str, Any] = {}
    failures: list[str] = []
    replay_available = True
    for task, (trajectory, receipt) in sorted(pairs.items()):
        result = audit_task(trajectory, receipt, task)
        tasks[task] = result
        failures.extend(result["failures"])
        replay_available = replay_available and result["replay_state_available"]
    provider_delivery_totals = {
        "delivery_count": sum(
            int(result.get("provider_delivery_totals", {}).get("delivery_count") or 0)
            for result in tasks.values()
        ),
        "visible_chars": sum(
            int(result.get("provider_delivery_totals", {}).get("visible_chars") or 0)
            for result in tasks.values()
        ),
        "claim_count": sum(
            int(result.get("provider_delivery_totals", {}).get("claim_count") or 0)
            for result in tasks.values()
        ),
        "timely_count": sum(
            int(result.get("provider_delivery_totals", {}).get("timely_count") or 0)
            for result in tasks.values()
        ),
        "late_count": sum(
            int(result.get("provider_delivery_totals", {}).get("late_count") or 0)
            for result in tasks.values()
        ),
        "predictive_count": sum(
            int(result.get("provider_delivery_totals", {}).get("predictive_count") or 0)
            for result in tasks.values()
        ),
        "duplicate_count": sum(
            int(result.get("provider_delivery_totals", {}).get("duplicate_count") or 0)
            for result in tasks.values()
        ),
    }
    deterministic_ok = bool(tasks) and not failures
    return {
        "schema": "gt.central_trajectory_audit.v1",
        "run_root": str(root.resolve()),
        "task_count": len(tasks),
        "tasks": tasks,
        "provider_delivery_totals": provider_delivery_totals,
        "failures": failures,
        "audit_status": (
            "DETERMINISTIC_AUDIT_CERTIFIED" if deterministic_ok else "DETERMINISTIC_AUDIT_FAILED"
        ),
        "certification": {
            "deterministic_integrity": "CERTIFIED" if deterministic_ok else "FAILED",
            "replay_state_available": replay_available,
            "model_causality": "COUNTERFACTUAL_CERTIFIED"
            if deterministic_ok and replay_available
            else "UNIDENTIFIABLE",
            "causality_basis": (
                "counterfactual_replay_required; anchor_following_is_not_causal_proof"
            ),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_root", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    report = audit_run_root(args.run_root)
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.json:
        args.json.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    print(
        "TRAJECTORY_AUDIT_CERTIFIED"
        if report["audit_status"] == "DETERMINISTIC_AUDIT_CERTIFIED"
        else "TRAJECTORY_AUDIT_FAILED"
    )
    print(
        "MODEL_CAUSALITY_CERTIFIED"
        if report["certification"]["model_causality"] == "COUNTERFACTUAL_CERTIFIED"
        else "MODEL_CAUSALITY_UNIDENTIFIABLE"
    )
    return 0 if report["audit_status"] == "DETERMINISTIC_AUDIT_CERTIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
