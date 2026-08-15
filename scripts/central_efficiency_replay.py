#!/usr/bin/env python3
"""Replay archived receipts through outcome-preserving GT policy boundaries."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import copy
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.gt_central_agent import MiniSweCentralAgent, _provider_request_receipt
from gt_engine.central_runtime import (
    ValidationAuthority,
    classify_validation_command,
    explicit_check_commands,
)
from gt_engine.preflight import adapt_proposed_action
from gt_engine.provider_view import (
    DEFAULT_MIN_COMPACTION_SAVINGS_CHARS,
    DEFAULT_MIN_COMPACTION_SAVINGS_RATIO,
    DEFAULT_SOFT_COMPACTION_TARGET_CHARS,
    DEFAULT_SOFT_COMPACTION_TRIGGER_CHARS,
    ProviderViewSession,
    RequestBudget,
    build_provider_view,
    provider_compaction_required,
    provider_request_budget,
)

_FAILURE_FEATURES = frozenset({"covering_red", "submit_refusal", "GT_SS_SUBMIT_RED"})


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _task_name(trajectory_path: Path) -> str:
    trial = trajectory_path.parent.parent.name
    return trial.split("__", 1)[0]


def _request_budget(row: dict[str, Any]) -> RequestBudget | None:
    payload = dict(row.get("request_budget") or {})
    payload.pop("within_limit", None)
    required = {
        "context_limit_tokens",
        "counted_tokens",
        "conservative_tokens",
        "effective_tokens",
        "hard_prompt_limit",
        "remaining_tokens",
        "counter_source",
    }
    if not required.issubset(payload):
        return None
    return RequestBudget(**{key: payload[key] for key in required})


def _last_model_request(messages: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    prefix: list[dict[str, Any]] = []
    latest: list[dict[str, Any]] | None = None
    for message in messages:
        if message.get("role") == "assistant":
            latest = list(prefix)
        prefix.append(message)
    return latest


def _annotate_replay_operations(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Reconstruct runtime-private typed observation metadata on a copy."""

    annotated = copy.deepcopy(messages)
    call = 0
    for index, message in enumerate(annotated):
        if message.get("role") != "assistant":
            continue
        call += 1
        actions = tuple((message.get("extra") or {}).get("actions") or ())
        proposals = tuple(
            adapt_proposed_action(
                action,
                source_revision="replay",
                workspace_revision="replay",
                model_call=call,
                batch_index=batch_index,
                batch_size=len(actions),
            )
            for batch_index, action in enumerate(actions)
        )
        tool_indices: list[int] = []
        cursor = index + 1
        while cursor < len(annotated) and annotated[cursor].get("role") == "tool":
            tool_indices.append(cursor)
            cursor += 1
        for tool_index, proposed in zip(tool_indices, proposals, strict=False):
            tool = annotated[tool_index]
            extra = dict(tool.get("extra") or {})
            extra.update(
                {
                    "operation": proposed.operation.value,
                    "action_id": proposed.action_id,
                    "observation_index": tool_index,
                }
            )
            tool["extra"] = extra
    return annotated


def _replay_provider_view(messages: list[dict[str, Any]]) -> dict[str, Any]:
    annotated = _annotate_replay_operations(messages)
    session = ProviderViewSession()
    raw_chars = 0
    provider_chars = 0
    call_count = 0
    bounded: dict[tuple[int, str], dict[str, Any]] = {}
    final_view: list[dict[str, Any]] | None = None
    unique_reasoning_removed = 0
    compaction_deferrals = 0
    for index, message in enumerate(annotated):
        if message.get("role") != "assistant":
            continue
        call_count += 1
        prefix = annotated[:index]
        view, metrics = session.project(prefix, active_state={})
        if (
            session.epoch == 0
            and metrics.output_chars > DEFAULT_SOFT_COMPACTION_TRIGGER_CHARS
        ):
            _preview, preview_metrics = build_provider_view(
                view,
                active_state={},
                trigger_chars=1,
                target_chars=DEFAULT_SOFT_COMPACTION_TARGET_CHARS,
                keep_recent_turns=2,
                transform=True,
                attach_state_frame=False,
            )
            savings = max(0, metrics.output_chars - preview_metrics.output_chars)
            ratio = savings / metrics.output_chars if metrics.output_chars else 0.0
            if (
                savings >= DEFAULT_MIN_COMPACTION_SAVINGS_CHARS
                and ratio >= DEFAULT_MIN_COMPACTION_SAVINGS_RATIO
            ):
                view, metrics = session.compact(
                    prefix,
                    active_state={},
                    target_chars=DEFAULT_SOFT_COMPACTION_TARGET_CHARS,
                    keep_recent_turns=2,
                    trigger_tokens=0,
                    trigger_kind="provider_view_chars",
                    trigger_chars=metrics.output_chars,
                )
            else:
                compaction_deferrals += 1
        raw_chars += metrics.raw_input_chars
        provider_chars += metrics.output_chars
        unique_reasoning_removed += metrics.unique_assistant_reasoning_chars_removed
        final_view = view
        for row in metrics.bounded_observations:
            identity = (
                int(row.get("observation_index") or 0),
                str(row.get("full_sha256") or ""),
            )
            bounded.setdefault(identity, dict(row))
    return {
        "model_calls_replayed": call_count,
        "raw_provider_view_chars_cumulative": raw_chars,
        "projected_provider_view_chars_cumulative": provider_chars,
        "projected_provider_view_chars_avoided": max(0, raw_chars - provider_chars),
        "projected_provider_view_reduction_ratio": (
            round((raw_chars - provider_chars) / raw_chars, 6) if raw_chars else 0.0
        ),
        "bounded_unique_observations": len(bounded),
        "bounded_unique_observation_chars_removed": sum(
            int(row.get("omitted_chars") or 0) for row in bounded.values()
        ),
        "projected_compaction_epochs": session.epoch,
        "compaction_deferrals": compaction_deferrals,
        "assistant_reasoning_chars_removed": unique_reasoning_removed,
        "final_provider_view": final_view,
    }


def replay_run(
    root: Path,
    *,
    reserve_tokens: int = 131_072,
    model: Any | None = None,
    model_name: str = "deepseek-v4-flash",
    context_limit_tokens: int = 1_048_576,
    hard_ratio: float = 0.90,
) -> dict[str, Any]:
    tasks: dict[str, Any] = {}
    invalid_receipts = 0
    invalid_actions: set[tuple[str, int]] = set()
    declared_preserved = 0
    avoided_partial_execs = 0
    projected_partial_execs = 0
    projected_epochs = 0
    raw_provider_chars = 0
    projected_provider_chars = 0
    bounded_unique_observations = 0

    for trajectory_path in sorted(root.rglob("miniswe_trajectory.json")):
        receipt_path = trajectory_path.with_name("central_receipt.json")
        if not receipt_path.exists():
            continue
        task = _task_name(trajectory_path)
        trajectory = _load(trajectory_path)
        receipt = _load(receipt_path)
        messages = list(trajectory.get("messages") or ())
        provider_view_replay = _replay_provider_view(messages)
        raw_provider_chars += int(
            provider_view_replay["raw_provider_view_chars_cumulative"]
        )
        projected_provider_chars += int(
            provider_view_replay["projected_provider_view_chars_cumulative"]
        )
        bounded_unique_observations += int(
            provider_view_replay["bounded_unique_observations"]
        )
        instruction = next(
            (
                str(message.get("content") or "")
                for message in messages
                if message.get("role") == "user"
            ),
            "",
        )
        checks = explicit_check_commands(instruction)
        validation_log = (receipt.get("features") or {}).get("validation_log") or []
        authority_by_action = {
            int(row.get("action") or 0): classify_validation_command(
                str(row.get("command") or ""), checks
            ).authority
            for row in validation_log
        }
        task_invalid = 0
        task_declared = 0
        for feature_receipt in (receipt.get("features") or {}).get("receipts") or ():
            if (
                not feature_receipt.get("model_visible")
                or feature_receipt.get("boundary") != "test_result"
                or feature_receipt.get("feature_id") not in _FAILURE_FEATURES
            ):
                continue
            action = int(feature_receipt.get("action") or 0)
            if authority_by_action.get(action) is ValidationAuthority.DECLARED:
                task_declared += 1
            else:
                task_invalid += 1
                invalid_actions.add((task, action))
        invalid_receipts += task_invalid
        declared_preserved += task_declared

        metrics = receipt.get("metrics") or {}
        old_completion_execs = int(metrics.get("completion_probe_execs") or 0)
        partial = str(metrics.get("completion_plan_status") or "") != "complete"
        if partial:
            avoided_partial_execs += old_completion_execs
        else:
            projected_partial_execs += 0

        call_contexts = list(receipt.get("model_call_contexts") or ())
        budget_source = "recorded_transformed_request"
        budgets: list[RequestBudget] = []
        last_request = provider_view_replay.pop("final_provider_view") or _last_model_request(
            messages
        )
        if model is not None and last_request is not None:
            provider_messages, _, _, _ = _provider_request_receipt(model, last_request)
            raw_budget = provider_request_budget(
                provider_messages,
                model_name=model_name,
                context_limit_tokens=context_limit_tokens,
                hard_ratio=hard_ratio,
            )
            # Runtime guidance is injected into a copy rather than the durable
            # trajectory. Inflate the reconstructed final request by the
            # largest archived advisory as a conservative upper bound.
            advisory_reserve = max(
                (int(row.get("runtime_advisory_chars") or 0) for row in call_contexts),
                default=0,
            )
            budgets = [
                RequestBudget(
                    context_limit_tokens=raw_budget.context_limit_tokens,
                    counted_tokens=raw_budget.counted_tokens,
                    conservative_tokens=raw_budget.conservative_tokens + advisory_reserve,
                    effective_tokens=raw_budget.effective_tokens + advisory_reserve,
                    hard_prompt_limit=raw_budget.hard_prompt_limit,
                    remaining_tokens=raw_budget.remaining_tokens - advisory_reserve,
                    counter_source=raw_budget.counter_source + "+advisory_upper_bound",
                )
            ]
            budget_source = "reconstructed_raw_final_provider_request"
        else:
            budgets = [
                budget
                for row in call_contexts
                if (budget := _request_budget(row)) is not None
            ]
        needs_epoch = any(
            provider_compaction_required(budget, reserve_tokens=reserve_tokens)
            for budget in budgets
        )
        projected_epochs += int(needs_epoch)
        tasks[task] = {
            "invalid_visible_failure_receipts": task_invalid,
            "declared_visible_failure_receipts_preserved": task_declared,
            "old_completion_probe_execs": old_completion_execs,
            "projected_completion_probe_execs": 0 if partial else old_completion_execs,
            "minimum_provider_headroom_tokens": min(
                (budget.remaining_tokens for budget in budgets), default=None
            ),
            "provider_budget_evidence": budget_source,
            "projected_compaction_epoch": needs_epoch,
            "provider_view_replay": provider_view_replay,
        }

    return {
        "task_count": len(tasks),
        "tasks": tasks,
        "invalid_visible_failure_receipts": invalid_receipts,
        "invalid_visible_failure_actions": len(invalid_actions),
        "declared_visible_failure_receipts_preserved": declared_preserved,
        "avoided_partial_completion_probe_execs": avoided_partial_execs,
        "projected_partial_completion_probe_execs": projected_partial_execs,
        "projected_compaction_epochs": projected_epochs,
        "provider_view_replay_compaction_epochs": sum(
            int(row["provider_view_replay"]["projected_compaction_epochs"])
            for row in tasks.values()
        ),
        "provider_view_compaction_deferrals": sum(
            int(row["provider_view_replay"]["compaction_deferrals"])
            for row in tasks.values()
        ),
        "provider_view_assistant_reasoning_chars_removed": sum(
            int(row["provider_view_replay"]["assistant_reasoning_chars_removed"])
            for row in tasks.values()
        ),
        "raw_provider_view_chars_cumulative": raw_provider_chars,
        "projected_provider_view_chars_cumulative": projected_provider_chars,
        "projected_provider_view_chars_avoided": max(
            0, raw_provider_chars - projected_provider_chars
        ),
        "projected_provider_view_reduction_ratio": (
            round(
                (raw_provider_chars - projected_provider_chars) / raw_provider_chars,
                6,
            )
            if raw_provider_chars
            else 0.0
        ),
        "bounded_unique_observations": bounded_unique_observations,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_root", type=Path)
    parser.add_argument("--reserve-tokens", type=int, default=131_072)
    parser.add_argument("--model-name", default="deepseek-v4-flash")
    parser.add_argument("--context-limit-tokens", type=int, default=1_048_576)
    parser.add_argument("--hard-ratio", type=float, default=0.90)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as directory:
        model = MiniSweCentralAgent(
            logs_dir=Path(directory), model_name=args.model_name
        )._build_model()
        result = replay_run(
            args.run_root.resolve(),
            reserve_tokens=args.reserve_tokens,
            model=model,
            model_name=args.model_name,
            context_limit_tokens=args.context_limit_tokens,
            hard_ratio=args.hard_ratio,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    if not result["task_count"]:
        print("ARCHIVED_EFFICIENCY_REPLAY_EMPTY")
        return 2
    print("ARCHIVED_EFFICIENCY_REPLAY_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
