"""Freeze and compare one manifest-matched Mini-SWE GT witness.

This is deliberately descriptive.  A single stochastic task can prove that the
provider-bound treatment executed and expose concrete efficiency deltas; it
cannot support a population solve-rate or causal-effect estimate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gt_engine.bridge import bash_edit_target

BASELINE_AGENT = "eval.miniswe_agent:MiniSweAgent"
CANDIDATE_AGENT = "eval.miniswe_agent:MiniSweGtAgent"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path.name}")
    return value


def _trial(result: dict[str, Any], task_id: str) -> dict[str, Any]:
    if result.get("task_name") == task_id:
        return result
    matches = [
        row
        for row in result.get("trial_results", [])
        if isinstance(row, dict) and row.get("task_name") == task_id
    ]
    if len(matches) != 1:
        raise ValueError(f"expected one result row for {task_id}, got {len(matches)}")
    return matches[0]


def _fingerprint(trajectory: dict[str, Any]) -> str:
    values: set[str] = set()
    for message in trajectory.get("messages", []):
        response = (message.get("extra") or {}).get("response") or {}
        value = response.get("system_fingerprint")
        if value:
            values.add(str(value))
    if len(values) != 1:
        raise ValueError(f"expected one system_fingerprint, got {sorted(values)}")
    return values.pop()


def _trajectory_metrics(trajectory: dict[str, Any]) -> dict[str, Any]:
    action_count = 0
    exploration_actions = 0
    raw_bytes_before_first_edit = 0
    before_first_edit = True
    for message in trajectory.get("messages", []):
        if message.get("role") == "assistant":
            for action in (message.get("extra") or {}).get("actions") or []:
                command = str(action.get("command") or "")
                action_count += 1
                if before_first_edit and bash_edit_target(command) is None:
                    exploration_actions += 1
                if bash_edit_target(command) is not None:
                    before_first_edit = False
        elif message.get("role") == "tool" and before_first_edit:
            extra = message.get("extra") or {}
            raw = extra.get("raw_output")
            if raw is None:
                raw = message.get("content") or ""
            raw_bytes_before_first_edit += len(str(raw).encode("utf-8"))
    info = trajectory.get("info") or {}
    return {
        "api_calls": int((info.get("model_stats") or {}).get("api_calls") or 0),
        "action_count": action_count,
        "exploration_actions_before_first_edit": exploration_actions,
        "raw_bytes_before_first_edit": raw_bytes_before_first_edit,
    }


def _prompt_hashes(trajectory: dict[str, Any]) -> tuple[str, str]:
    prompts = {
        str(message.get("role")): str(message.get("content") or "")
        for message in trajectory.get("messages", [])
        if message.get("role") in {"system", "user"}
    }
    if set(prompts) != {"system", "user"}:
        raise ValueError("trajectory does not contain the initial system/user prompt")
    return tuple(
        hashlib.sha256(prompts[role].encode("utf-8")).hexdigest()
        for role in ("system", "user")
    )


def _identity(
    trial: dict[str, Any], trajectory: dict[str, Any], *, expected_agent: str
) -> dict[str, Any]:
    config = trial.get("config") or {}
    agent = config.get("agent") or {}
    if agent.get("name") != expected_agent:
        raise ValueError(
            f"agent identity mismatch: expected {expected_agent}, got {agent.get('name')}"
        )
    info = trajectory.get("info") or {}
    runtime = info.get("config") or {}
    model = runtime.get("model") or {}
    agent_runtime = runtime.get("agent") or {}
    task = trial.get("task_id") or {}
    system_prompt_sha256, task_prompt_sha256 = _prompt_hashes(trajectory)
    return {
        "task_id": trial.get("task_name"),
        "task_checksum": trial.get("task_checksum"),
        "dataset_commit": task.get("git_commit_id"),
        "agent": agent.get("name"),
        "model": agent.get("model_name"),
        "resolved_model": model.get("model_name"),
        "system_fingerprint": _fingerprint(trajectory),
        "mini_swe_version": info.get("mini_version"),
        "temperature": (model.get("model_kwargs") or {}).get("temperature"),
        "step_limit": agent_runtime.get("step_limit"),
        "cost_limit": agent_runtime.get("cost_limit"),
        "command_timeout_seconds": (runtime.get("environment") or {}).get("timeout"),
        "agent_timeout_multiplier": config.get("agent_timeout_multiplier"),
        "system_prompt_sha256": system_prompt_sha256,
        "task_prompt_sha256": task_prompt_sha256,
        "trajectory_format": trajectory.get("trajectory_format"),
    }


def _reward(trial: dict[str, Any]) -> float:
    rewards = (trial.get("verifier_result") or {}).get("rewards") or {}
    if "reward" not in rewards:
        raise ValueError("independent verifier reward is missing")
    return float(rewards["reward"])


def build_baseline_receipt(root: Path, task_id: str) -> dict[str, Any]:
    root = root.resolve()
    merged_path = root / "merged.json"
    trajectory_path = root / f"{task_id}_trajectory.json"
    summary_path = root / "SUMMARY.md"
    if not merged_path.is_file() or not trajectory_path.is_file() or not summary_path.is_file():
        raise ValueError("baseline requires SUMMARY.md, merged.json, and the selected trajectory")
    merged = _load(merged_path)
    trajectory = _load(trajectory_path)
    trial = _trial(merged, task_id)
    identity = _identity(trial, trajectory, expected_agent=BASELINE_AGENT)
    summary = summary_path.read_text(encoding="utf-8", errors="replace")
    run_match = re.search(r"GHA run:\*{0,2}\s*`(\d+)`", summary)
    commit_match = re.search(
        r"Branch/commit:\*{0,2}.*?@\s*`([0-9a-f]{7,40})`", summary
    )
    if not run_match or not commit_match:
        raise ValueError("baseline summary lacks GHA run or harness commit identity")
    return {
        "schema": "gt.phase2.single_witness_baseline.v1",
        **identity,
        "trial_id": trial.get("id"),
        "reward": _reward(trial),
        "gt_enabled": False,
        "trajectory_sha256": _sha256(trajectory_path),
        "merged_results_sha256": _sha256(merged_path),
        "summary_sha256": _sha256(summary_path),
        "baseline_github_actions_run_id": run_match.group(1),
        "baseline_harness_commit": commit_match.group(1),
        "metrics": _trajectory_metrics(trajectory),
        "image_digest_available": False,
        "limitations": [
            "the archived control records Docker execution and the exact task "
            "checksum but not the resolved container image digest"
        ],
    }


def _candidate_files(root: Path, task_id: str) -> tuple[Path, Path]:
    results = []
    for path in root.rglob("result.json"):
        try:
            _trial(_load(path), task_id)
        except ValueError:
            continue
        results.append(path)
    if len(results) != 1:
        raise ValueError(f"expected one candidate result.json, got {len(results)}")
    trajectories = list(root.rglob("miniswe_trajectory.json"))
    if len(trajectories) != 1:
        raise ValueError(
            f"expected one candidate miniswe_trajectory.json, got {len(trajectories)}"
        )
    return results[0], trajectories[0]


def analyze_single_witness(
    baseline: dict[str, Any], candidate_root: Path
) -> dict[str, Any]:
    if baseline.get("schema") != "gt.phase2.single_witness_baseline.v1":
        raise ValueError("baseline receipt schema mismatch")
    task_id = str(baseline.get("task_id") or "")
    result_path, trajectory_path = _candidate_files(candidate_root.resolve(), task_id)
    result = _load(result_path)
    trajectory = _load(trajectory_path)
    trial = _trial(result, task_id)
    candidate = _identity(trial, trajectory, expected_agent=CANDIDATE_AGENT)
    controlled = (
        "task_id",
        "task_checksum",
        "model",
        "resolved_model",
        "system_fingerprint",
        "mini_swe_version",
        "temperature",
        "step_limit",
        "cost_limit",
        "command_timeout_seconds",
        "agent_timeout_multiplier",
        "task_prompt_sha256",
        "trajectory_format",
        "dataset_commit",
    )
    mismatches = {
        key: {"baseline": baseline.get(key), "candidate": candidate.get(key)}
        for key in controlled
        if baseline.get(key) != candidate.get(key)
    }
    if mismatches:
        raise ValueError("manifest mismatch: " + ", ".join(sorted(mismatches)))
    candidate_metrics = _trajectory_metrics(trajectory)
    candidate_record = {
        **candidate,
        "trial_id": trial.get("id"),
        "reward": _reward(trial),
        "gt_enabled": True,
        "trajectory_sha256": _sha256(trajectory_path),
        "result_sha256": _sha256(result_path),
        "metrics": candidate_metrics,
    }
    baseline_metrics = baseline.get("metrics") or {}
    deltas = {
        key: candidate_metrics[key] - int(baseline_metrics[key])
        for key in (
            "api_calls",
            "action_count",
            "exploration_actions_before_first_edit",
            "raw_bytes_before_first_edit",
        )
    }
    deltas["reward"] = candidate_record["reward"] - float(baseline["reward"])
    return {
        "schema": "gt.phase2.single_witness_analysis.v1",
        "manifest_identical": True,
        "matched_tasks": 1,
        "baseline": baseline,
        "candidate": candidate_record,
        "deltas": deltas,
        "inferential_claim": False,
        "verdict": (
            "non_regressing_witness"
            if candidate_record["reward"] >= float(baseline["reward"])
            else "regressing_witness"
        ),
        "limitations": [
            *list(baseline.get("limitations") or []),
            "one stochastic matched task is a descriptive engineering witness, "
            "not a population solve-rate estimate",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("freeze-baseline")
    freeze.add_argument("--root", type=Path, required=True)
    freeze.add_argument("--task", required=True)
    freeze.add_argument("--out", type=Path, required=True)
    analyze = commands.add_parser("analyze")
    analyze.add_argument("--baseline-receipt", type=Path, required=True)
    analyze.add_argument("--candidate-root", type=Path, required=True)
    analyze.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "freeze-baseline":
        output = build_baseline_receipt(args.root, args.task)
    else:
        output = analyze_single_witness(
            _load(args.baseline_receipt), args.candidate_root
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
