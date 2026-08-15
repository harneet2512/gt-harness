"""Compare two archived central-runtime runs without calling a model.

The report distinguishes a model trajectory divergence that occurs before a
grounded GT payload could be visible from later provider-view, guidance, or
controller differences.  It reads only archived trajectories and receipts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gt_engine.delivery_audit import (  # noqa: E402
    audit_provider_deliveries,
    collect_provider_deliveries,
)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _messages(trajectory: dict[str, Any]) -> list[dict[str, Any]]:
    messages = trajectory.get("messages") or []
    return [item for item in messages if isinstance(item, dict)]


def _actions_by_call(trajectory: dict[str, Any]) -> dict[int, tuple[str, ...]]:
    result: dict[int, tuple[str, ...]] = {}
    call = 0
    for message in _messages(trajectory):
        if message.get("role") != "assistant":
            continue
        call += 1
        actions = (message.get("extra") or {}).get("actions") or []
        result[call] = tuple(
            str(action.get("command") or action.get("cmd") or "")
            for action in actions
            if isinstance(action, dict)
        )
    return result


def _task_name(trajectory_path: Path) -> str:
    for parent in trajectory_path.parents:
        marker = "-task-"
        if marker in parent.name:
            return parent.name.split(marker, 1)[1]
    trial = trajectory_path.parent.parent.name
    return trial.split("__", 1)[0]


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pair_identity(pair: tuple[Path, Path]) -> tuple[str, str]:
    trajectory_path, receipt_path = pair
    return _file_sha256(trajectory_path), _file_sha256(receipt_path)


def _discover(root: Path) -> dict[str, tuple[Path, Path]]:
    discovered: dict[str, tuple[Path, Path]] = {}
    for trajectory_path in sorted(root.rglob("miniswe_trajectory.json")):
        receipt_path = trajectory_path.parent / "central_receipt.json"
        if not receipt_path.exists():
            continue
        task = _task_name(trajectory_path)
        candidate = (trajectory_path, receipt_path)
        if task in discovered:
            if _pair_identity(discovered[task]) == _pair_identity(candidate):
                continue
            raise ValueError(f"conflicting duplicate task {task!r} below {root}")
        discovered[task] = candidate
    if not discovered:
        raise ValueError(f"no trajectory/receipt pairs below {root}")
    return discovered


def _contexts(receipt: dict[str, Any]) -> dict[int, dict[str, Any]]:
    rows: dict[int, dict[str, Any]] = {}
    for raw in receipt.get("model_call_contexts") or []:
        if not isinstance(raw, dict):
            continue
        call = raw.get("call")
        if not isinstance(call, int) or call < 1:
            continue
        if call in rows:
            raise ValueError(f"duplicate model-call context {call}")
        rows[call] = raw
    return rows


def _request_identity(context: dict[str, Any]) -> tuple[str, str]:
    return (
        str(context.get("provider_messages_sha256") or ""),
        str(context.get("request_payload_sha256") or ""),
    )


def _control_request_identity(context: dict[str, Any]) -> tuple[str, str]:
    """Return the provider view before GT text was attached."""

    return (
        str(
            context.get("control_provider_messages_sha256")
            or context.get("provider_messages_sha256")
            or ""
        ),
        str(
            context.get("control_request_payload_sha256")
            or context.get("request_payload_sha256")
            or ""
        ),
    )


def _first_visible_call(receipt: dict[str, Any]) -> int | None:
    calls = []
    for item in collect_provider_deliveries(receipt):
        call = item.get("delivered_before_call") or item.get("first_eligible_call")
        if isinstance(call, int) and call > 0:
            calls.append(call)
    return min(calls) if calls else None


def _first_action_difference(
    left: dict[int, tuple[str, ...]], right: dict[int, tuple[str, ...]]
) -> tuple[int | None, dict[str, Any] | None]:
    for call in sorted(set(left) | set(right)):
        if left.get(call, ()) != right.get(call, ()):
            return call, {
                "left_actions": list(left.get(call, ())),
                "right_actions": list(right.get(call, ())),
            }
    return None, None


def _request_differences(
    left: dict[int, dict[str, Any]], right: dict[int, dict[str, Any]]
) -> list[int]:
    return [
        call
        for call in sorted(set(left) | set(right))
        if _request_identity(left.get(call, {})) != _request_identity(right.get(call, {}))
    ]


def _control_request_differences(
    baseline: dict[int, dict[str, Any]], treatment: dict[int, dict[str, Any]]
) -> list[int]:
    return [
        call
        for call in sorted(set(baseline) | set(treatment))
        if _request_identity(baseline.get(call, {}))
        != _control_request_identity(treatment.get(call, {}))
    ]


def _context_summary(receipt: dict[str, Any]) -> dict[str, Any]:
    metrics = receipt.get("metrics") or {}
    _, delivery_failures, delivery_totals = audit_provider_deliveries(receipt)
    return {
        "compactions": int(metrics.get("context_compactions") or 0),
        "chars_elided": int(metrics.get("context_chars_elided") or 0),
        "gt_context_chars_added": int(metrics.get("total_gt_context_chars_added") or 0),
        "context_frontier_chars_added": int(metrics.get("context_frontier_chars_added") or 0),
        "context_frontier_deliveries": int(metrics.get("context_frontier_deliveries") or 0),
        "repository_intelligence_status": str(
            metrics.get("repository_intelligence_status") or "unreported"
        ),
        "effective_actions": int(metrics.get("effective_actions") or 0),
        "preflight_calls": int(metrics.get("preflight_calls") or 0),
        "preflight_dispositions": dict(metrics.get("preflight_applied_dispositions") or {}),
        "provider_delivery_count": delivery_totals["delivery_count"],
        "provider_visible_chars": delivery_totals["visible_chars"],
        "provider_delivery_claims": delivery_totals["claim_count"],
        "provider_delivery_timely": delivery_totals["timely_count"],
        "provider_delivery_late": delivery_totals["late_count"],
        "provider_delivery_predictive": delivery_totals["predictive_count"],
        "provider_delivery_duplicates": delivery_totals["duplicate_count"],
        "provider_delivery_failures": delivery_failures,
        "provider_delivery_surfaces": delivery_totals["surfaces"],
    }


def _frames(receipt: dict[str, Any]) -> list[dict[str, Any]]:
    decisions = (receipt.get("features") or {}).get("semantic_decisions") or {}
    frames = []
    for frame in decisions.get("frames") or []:
        if not isinstance(frame, dict):
            continue
        frames.append(
            {
                "feature_ids": list(frame.get("feature_ids") or []),
                "evidence_actions": list(frame.get("evidence_actions") or []),
                "materialized_for_call": frame.get("materialized_for_call"),
                "text": str(frame.get("text") or ""),
            }
        )
    return frames


def _accounting_complete(receipt: dict[str, Any]) -> bool:
    contexts = _contexts(receipt)
    _, delivery_failures, _totals = audit_provider_deliveries(receipt)
    return (
        bool(contexts)
        and all(all(_request_identity(row)) for row in contexts.values())
        and not delivery_failures
    )


def _compare_task(
    left_trajectory: dict[str, Any],
    left_receipt: dict[str, Any],
    right_trajectory: dict[str, Any],
    right_receipt: dict[str, Any],
) -> dict[str, Any]:
    left_actions = _actions_by_call(left_trajectory)
    right_actions = _actions_by_call(right_trajectory)
    first_call, first_detail = _first_action_difference(left_actions, right_actions)
    left_contexts, right_contexts = _contexts(left_receipt), _contexts(right_receipt)
    left_visible, right_visible = (
        _first_visible_call(left_receipt),
        _first_visible_call(right_receipt),
    )
    visible_candidates = [item for item in (left_visible, right_visible) if item is not None]
    earliest_visible = min(visible_candidates) if visible_candidates else None
    return {
        "first_divergent_model_call": first_call,
        "first_divergent_actions": first_detail,
        "first_divergence_precedes_visible_evidence": bool(
            first_call is not None and (earliest_visible is None or first_call < earliest_visible)
        ),
        "request_differences": _request_differences(left_contexts, right_contexts),
        "control_request_differences": _control_request_differences(
            left_contexts, right_contexts
        ),
        "control_request_accounting_complete": bool(right_contexts)
        and all(
            context.get("control_provider_messages_sha256")
            and context.get("control_request_payload_sha256")
            for context in right_contexts.values()
        ),
        "guidance": {
            "left_first_visible_call": left_visible,
            "right_first_visible_call": right_visible,
            "left_frames": _frames(left_receipt),
            "right_frames": _frames(right_receipt),
        },
        "provider_deliveries": {
            "left": _context_summary(left_receipt).get("provider_delivery_surfaces", {}),
            "right": _context_summary(right_receipt).get("provider_delivery_surfaces", {}),
        },
        "left": _context_summary(left_receipt),
        "right": _context_summary(right_receipt),
        "accounting_complete": _accounting_complete(left_receipt)
        and _accounting_complete(right_receipt),
    }


def compare_run_roots(left_root: Path, right_root: Path) -> dict[str, Any]:
    """Return a deterministic, read-only comparison of archived run roots."""
    left_pairs, right_pairs = _discover(left_root), _discover(right_root)
    tasks: dict[str, dict[str, Any]] = {}
    for task in sorted(set(left_pairs) | set(right_pairs)):
        if task not in left_pairs or task not in right_pairs:
            tasks[task] = {
                "missing_left": task not in left_pairs,
                "missing_right": task not in right_pairs,
                "accounting_complete": False,
            }
            continue
        left_trajectory_path, left_receipt_path = left_pairs[task]
        right_trajectory_path, right_receipt_path = right_pairs[task]
        tasks[task] = _compare_task(
            _load_json(left_trajectory_path),
            _load_json(left_receipt_path),
            _load_json(right_trajectory_path),
            _load_json(right_receipt_path),
        )
    return {
        "schema": "gt.central_run_diff.v1",
        "left_root": str(left_root),
        "right_root": str(right_root),
        "task_count": len(tasks),
        "tasks": tasks,
        "all_accounting_complete": bool(tasks)
        and all(item.get("accounting_complete") for item in tasks.values()),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("left_root", type=Path)
    parser.add_argument("right_root", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    report = compare_run_roots(args.left_root.resolve(), args.right_root.resolve())
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.json:
        args.json.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["all_accounting_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
