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
    candidates = (
        ((payload.get("verifier_result") or {}).get("rewards") or {}).get("reward"),
        (payload.get("rewards") or {}).get("reward"),
        payload.get("reward"),
    )
    for value in candidates:
        if value in (0, 0.0, 1, 1.0):
            return int(value)
    return None


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
    if len(result_paths) != 1:
        raise ValueError(f"expected exactly one runner result, found {len(result_paths)}")
    result_bytes = result_paths[0].read_bytes()
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
        "runner_result_path": str(result_paths[0].relative_to(root)).replace("\\", "/"),
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
