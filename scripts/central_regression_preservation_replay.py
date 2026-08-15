#!/usr/bin/env python3
"""Replay the two workflow-31078501162 regression boundaries provider-free.

This script does not replay the language model and makes no outcome claim. It
proves that archived task text and provider history now cross the repaired
task-resource and context-budget boundaries deterministically.
"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
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
from gt_engine.central_runtime import task_deliverable_paths
from gt_engine.completion import CompletionStatus, compile_completion_plan
from gt_engine.provider_view import (
    ProviderViewSession,
    provider_compaction_required,
    provider_request_budget,
)
from gt_engine.task_contract import extract_task_resources


def _trajectory(root: Path, task: str) -> Path:
    matches = sorted(
        path
        for path in root.rglob("miniswe_trajectory.json")
        if task in path.as_posix()
    )
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one {task} trajectory below {root}, found {len(matches)}"
        )
    return matches[0]


def _messages(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    messages = list(payload.get("messages") or ())
    if messages and messages[-1].get("role") == "exit":
        messages.pop()
    return messages


def _scheduler_witness(root: Path) -> dict[str, Any]:
    messages = _messages(_trajectory(root, "llm-inference-batching-scheduler"))
    instruction = next(
        str(message.get("content") or "")
        for message in messages
        if message.get("role") == "user"
    )
    selected = {
        resource.path: {
            "role": resource.role.value,
            "confidence": resource.confidence,
        }
        for resource in extract_task_resources(instruction)
        if "task_file/" in resource.path
    }
    expected = {
        "task_file/input_data/requests_bucket_1.jsonl": "input",
        "task_file/input_data/requests_bucket_2.jsonl": "input",
        "task_file/output_data/plan_b1.jsonl": "output",
        "task_file/output_data/plan_b2.jsonl": "output",
    }
    for path, role in expected.items():
        row = selected.get(path) or {}
        if row.get("role") != role or float(row.get("confidence") or 0.0) < 0.8:
            raise RuntimeError(f"scheduler resource classification failed: {path}: {row}")
    deliverables = task_deliverable_paths(instruction)
    expected_outputs = (
        "task_file/output_data/plan_b1.jsonl",
        "task_file/output_data/plan_b2.jsonl",
    )
    if deliverables != expected_outputs:
        raise RuntimeError(f"scheduler deliverable projection failed: {deliverables}")
    plan = compile_completion_plan(instruction, cwd="/app")
    probes = tuple(
        predicate
        for predicate in plan.predicates
        if predicate.kind == "required_output_exists"
    )
    if (
        plan.status is not CompletionStatus.PARTIAL
        or plan.executable
        or len(probes) != 2
        or any(predicate.obligation_ids for predicate in probes)
    ):
        raise RuntimeError("scheduler progress probes could claim false completion")
    return {
        "resources": selected,
        "deliverables": list(deliverables),
        "completion_status": plan.status.value,
        "completion_executable": plan.executable,
        "output_probe_count": len(probes),
    }


def _compressor_witness(
    root: Path,
    *,
    model_name: str,
    context_limit_tokens: int,
    hard_ratio: float,
) -> dict[str, Any]:
    messages = _messages(_trajectory(root, "write-compressor"))
    session = ProviderViewSession()
    with tempfile.TemporaryDirectory() as directory:
        model = MiniSweCentralAgent(
            logs_dir=Path(directory), model_name=model_name
        )._build_model()
        view, metrics = session.project(messages, active_state={})
        provider_messages, _, _, provider_chars = _provider_request_receipt(model, view)
        budget = provider_request_budget(
            provider_messages,
            model_name=model_name,
            context_limit_tokens=context_limit_tokens,
            hard_ratio=hard_ratio,
        )
        compaction_required = provider_compaction_required(budget)
        if compaction_required:
            view, metrics = session.compact(
                messages,
                active_state={},
                target_chars=200_000,
                keep_recent_turns=2,
                trigger_tokens=budget.effective_tokens,
            )
            provider_messages, _, _, provider_chars = _provider_request_receipt(model, view)
            budget = provider_request_budget(
                provider_messages,
                model_name=model_name,
                context_limit_tokens=context_limit_tokens,
                hard_ratio=hard_ratio,
            )
    if metrics.unique_assistant_reasoning_chars_removed:
        raise RuntimeError("provider replay removed assistant reasoning")
    if (
        compaction_required
        and metrics.bounded_observation_count < 1
        and metrics.old_tool_results_cleared < 1
    ):
        raise RuntimeError(
            "archived provider history was neither bounded nor old-tool compacted"
        )
    if not budget.within_limit:
        raise RuntimeError(f"repaired provider replay remains over budget: {budget}")
    return {
        "raw_history_chars": metrics.raw_input_chars,
        "provider_view_chars": metrics.output_chars,
        "provider_prepared_chars": provider_chars,
        "compaction_required": compaction_required,
        "compaction_epochs": len(session.receipts),
        "bounded_observations": metrics.bounded_observation_count,
        "old_tool_results_cleared": metrics.old_tool_results_cleared,
        "bounded_observation_chars_removed": (
            metrics.bounded_observation_chars_removed
        ),
        "assistant_reasoning_chars_removed": (
            metrics.unique_assistant_reasoning_chars_removed
        ),
        "request_budget": budget.as_dict(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_root", type=Path)
    parser.add_argument("--model-name", default="deepseek-v4-flash")
    parser.add_argument("--context-limit-tokens", type=int, default=1_048_576)
    parser.add_argument("--hard-ratio", type=float, default=0.90)
    args = parser.parse_args()
    root = args.run_root.resolve()
    if not root.is_dir():
        raise SystemExit(f"run root is not a directory: {root}")
    result = {
        "scheduler": _scheduler_witness(root),
        "write_compressor": _compressor_witness(
            root,
            model_name=args.model_name,
            context_limit_tokens=args.context_limit_tokens,
            hard_ratio=args.hard_ratio,
        ),
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    print("ARCHIVED_REGRESSION_REPLAY_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
