"""Extract shared deep metrics and compare central-runtime experiment arms."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from gt_engine.deep_metrics import compare_arms, extract_trajectory, render_delta_markdown


def _rewards(path: str) -> dict[str, int | float]:
    if not path:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "tasks" in payload:
        payload = payload["tasks"]
    return {
        str(task): (value.get("reward") if isinstance(value, dict) else value)
        for task, value in payload.items()
    }


def _harbor_results(path: str) -> dict[str, dict]:
    if not path:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "trial_results" in payload:
        rows = payload["trial_results"]
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = [payload]
    return {
        str(row.get("task_name")): row
        for row in rows
        if isinstance(row, dict) and row.get("task_name")
    }


def _find_receipt(root: Path | None, task: str) -> Path | None:
    if root is None:
        return None
    candidates = [root / f"{task}_central_receipt.json", root / task / "central_receipt.json"]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    matches = [path for path in root.rglob("central_receipt.json") if task in str(path)]
    return matches[0] if matches else None


def _load_arm(path: str) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return payload.get("tasks") or payload


def _task_name(path: Path) -> str:
    if path.name == "miniswe_trajectory.json" and "__" in path.parent.parent.name:
        return path.parent.parent.name.split("__", 1)[0]
    return path.name.removesuffix("_trajectory.json")


def _reward_beside_trajectory(path: Path) -> int | float | None:
    reward_path = path.parent.parent / "verifier" / "reward.txt"
    if not reward_path.exists():
        return None
    try:
        return float(reward_path.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def _result_beside_trajectory(path: Path) -> dict | None:
    result_path = path.parent.parent / "result.json"
    if not result_path.exists():
        return None
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else None


def _reward_from_result(result: dict | None) -> int | float | None:
    rewards = ((result or {}).get("verifier_result") or {}).get("rewards") or {}
    value = rewards.get("reward")
    return value if isinstance(value, (int, float)) else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    extract = sub.add_parser("extract")
    extract.add_argument("--name", required=True)
    extract.add_argument("--input", required=True)
    extract.add_argument("--receipts", default="")
    extract.add_argument("--rewards", default="")
    extract.add_argument("--results", default="")
    extract.add_argument("--tasks", default="")
    extract.add_argument("--output", required=True)
    compare = sub.add_parser("compare")
    compare.add_argument("--baseline", required=True)
    compare.add_argument("--shadow", default="")
    compare.add_argument("--treatment", required=True)
    compare.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    if args.command == "extract":
        root = Path(args.input)
        receipt_root = Path(args.receipts) if args.receipts else None
        rewards = _rewards(args.rewards)
        harbor_results = _harbor_results(args.results)
        requested = {item.strip() for item in args.tasks.split(",") if item.strip()}
        tasks = {}
        for path in sorted(root.rglob("*_trajectory.json")):
            task = _task_name(path)
            if requested and task not in requested:
                continue
            receipt_path = _find_receipt(receipt_root, task)
            if receipt_path is None and path.name == "miniswe_trajectory.json":
                adjacent = path.parent / "central_receipt.json"
                receipt_path = adjacent if adjacent.exists() else None
            harbor_result = harbor_results.get(task) or _result_beside_trajectory(path)
            reward = rewards.get(task)
            if reward is None:
                reward = _reward_from_result(harbor_result)
            if reward is None:
                reward = _reward_beside_trajectory(path)
            tasks[task] = extract_trajectory(
                path,
                task=task,
                reward=reward,
                receipt_path=receipt_path,
                harbor_result=harbor_result,
            )
        output = {"schema": "central-deep-metrics-v2", "arm": args.name, "tasks": tasks}
        Path(args.output).write_text(json.dumps(output, indent=2), encoding="utf-8")
        return 0 if tasks else 2

    baseline = _load_arm(args.baseline)
    treatment = _load_arm(args.treatment)
    comparisons = {"baseline_to_treatment": compare_arms(baseline, treatment)}
    if args.shadow:
        shadow = _load_arm(args.shadow)
        comparisons = {
            "baseline_to_shadow": compare_arms(baseline, shadow),
            "shadow_to_treatment": compare_arms(shadow, treatment),
            **comparisons,
        }
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "deep_delta.json").write_text(
        json.dumps({"schema": "central-deep-delta-v2", **comparisons}, indent=2),
        encoding="utf-8",
    )
    markdown = "\n".join(
        render_delta_markdown(name, comparison) for name, comparison in comparisons.items()
    )
    (output_dir / "DEEP_DELTA.md").write_text(markdown, encoding="utf-8")
    return 0 if comparisons["baseline_to_treatment"]["gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
