"""Normalize Harbor/Pier verifier output into the GT benchmark receipt contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any


def _reward(payload: dict[str, Any]) -> int | None:
    candidates: list[Any] = [
        ((payload.get("verifier_result") or {}).get("rewards") or {}).get("reward"),
        (payload.get("rewards") or {}).get("reward"),
        payload.get("reward"),
    ]
    # Pier/Harbor's canonical run-level result stores the official verifier
    # metric under stats.evals.<agent/model/dataset>.metrics.  Per-task GT jobs
    # have exactly one trial, but the eval key is intentionally dynamic.
    # Ignoring this schema turned real reward=1 results into ERROR receipts.
    stats = payload.get("stats") or {}
    evals = stats.get("evals") or {}
    if isinstance(evals, dict):
        for evaluation in evals.values():
            if not isinstance(evaluation, dict):
                continue
            metrics = evaluation.get("metrics") or []
            if not isinstance(metrics, list):
                continue
            candidates.extend(
                metric.get("reward")
                for metric in metrics
                if isinstance(metric, dict)
            )
    rewards = {
        int(value)
        for value in candidates
        if value in (0, 0.0, 1, 1.0)
    }
    # Conflicting representations are corruption, not permission to select a
    # convenient result.  The caller records an explicit ERROR receipt.
    return next(iter(rewards)) if len(rewards) == 1 else None


def standardize_result(
    *, root: Path, suite: str, task_id: str, source_sha: str
) -> dict[str, object]:
    if suite not in {"terminal-bench-2", "deepswe"}:
        raise ValueError(f"unsupported runner suite: {suite}")
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError("product source SHA must be exactly 40 lowercase hex characters")
    product_paths = sorted(root.rglob("agent/gt-run.json"))
    if len(product_paths) != 1:
        raise ValueError(f"expected exactly one GT product receipt, found {len(product_paths)}")
    product = json.loads(product_paths[0].read_text(encoding="utf-8"))
    if str(product.get("task_id") or "") != task_id:
        raise ValueError("GT product receipt task does not match runner task")
    result_paths = [
        path
        for path in sorted(root.rglob("result.json"))
        if "agent" not in path.parts and "verifier" not in path.parts
    ]
    # Harbor writes both the aggregate run result (with ``stats`` and
    # ``n_total_trials``) and a per-trial result.json.  The aggregate is the
    # official verifier output; treating the implementation detail as an
    # ambiguity incorrectly fails otherwise valid tasks.
    canonical_result_paths = []
    for path in result_paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if "stats" in payload and "n_total_trials" in payload:
            canonical_result_paths.append(path)
    if len(canonical_result_paths) == 1:
        result_path = canonical_result_paths[0]
    elif len(result_paths) == 1:
        result_path = result_paths[0]
    else:
        raise ValueError(
            f"expected exactly one canonical runner result, found {len(canonical_result_paths)} "
            f"among {len(result_paths)} result files"
        )
    result_bytes = result_path.read_bytes()
    runner_result = json.loads(result_bytes)
    reward = _reward(runner_result)
    receipt: dict[str, object] = {
        "schema": "gt.official_verifier_result.v1",
        "benchmark_suite": suite,
        "task_id": task_id,
        "product_source_sha": source_sha,
        "status": "GRADED" if reward is not None else "ERROR",
        "reward": reward,
        "solved": reward == 1 if reward is not None else None,
        "runner_result_sha256": hashlib.sha256(result_bytes).hexdigest(),
        "runner_result_path": str(result_path.relative_to(root)).replace("\\", "/"),
    }
    target = product_paths[0].with_name("official-verifier-result.json")
    temporary = target.with_suffix(f"{target.suffix}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, target)
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--suite", choices=("terminal-bench-2", "deepswe"), required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--source-sha", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    receipt = standardize_result(
        root=args.root,
        suite=args.suite,
        task_id=args.task_id,
        source_sha=args.source_sha,
    )
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt["status"] == "GRADED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
