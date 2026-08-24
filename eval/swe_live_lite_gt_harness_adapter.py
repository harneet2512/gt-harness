"""Direct-container adapter for SWE-Live Lite.

SWE-Live Lite provisions a repository image directly instead of exposing the
Harbor/Pier installed-agent protocol.  This module translates that container
contract into the same released ``gt-harness run`` invocation used by the
other benchmark suites.  It contains no repository-intelligence or agent
logic of its own.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path

from eval.harbor_gt_harness_adapter import CANONICAL_MINISWE_VERSION


def build_product_argv(
    *,
    instruction: str,
    root: str,
    model: str,
    base_url: str,
    task_id: str,
    source_sha: str,
    time_budget_seconds: int,
    max_iterations: int,
    output: str,
    state_dir: str,
    temperature: float = 1.0,
) -> list[str]:
    """Build the exact, shell-free production CLI invocation."""

    if not instruction.strip():
        raise ValueError("SWE-Live Lite instruction must not be empty")
    if not task_id.strip():
        raise ValueError("SWE-Live Lite task id must not be empty")
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError("product source SHA must be exactly 40 lowercase hex characters")
    if time_budget_seconds <= 0 or max_iterations <= 0:
        raise ValueError("time and iteration budgets must be positive")
    return [
        "gt-harness",
        "run",
        instruction,
        "--model",
        model,
        "--base-url",
        base_url,
        "--temperature",
        str(temperature),
        "--max-iterations",
        str(max_iterations),
        "--time-budget-seconds",
        str(time_budget_seconds),
        "--treatment",
        "groundtruth",
        "--root",
        root,
        "--state-dir",
        state_dir,
        "--run-id",
        "swe-live-lite-product",
        "--task-id",
        task_id,
        "--trial-id",
        "1",
        "--output",
        output,
    ]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run GT Harness in a SWE-Live Lite task image")
    parser.add_argument("--instruction-file", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--product-source-sha", required=True)
    parser.add_argument("--time-budget-seconds", required=True, type=int)
    parser.add_argument("--max-iterations", default=300, type=int)
    parser.add_argument("--temperature", default=1.0, type=float)
    parser.add_argument("--output", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--adapter-receipt", required=True)
    return parser


def _write_adapter_receipt(path: Path, *, args: argparse.Namespace) -> None:
    receipt = {
        "schema": "gt.benchmark_product_adapter.v1",
        "benchmark_suite": "swe-live-lite",
        "adapter": "eval.swe_live_lite_gt_harness_adapter",
        "agent_scaffold": "mini-swe-agent",
        "agent_scaffold_version": CANONICAL_MINISWE_VERSION,
        "requested_model": args.model.removeprefix("openai/"),
        "effective_model": args.model,
        "provider_route": "openrouter",
        "task_id": args.task_id,
        "attempt": 1,
        "product_command": "gt-harness run",
        "product_source_sha": args.product_source_sha,
        "time_budget_seconds": str(args.time_budget_seconds),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    instruction_path = Path(args.instruction_file)
    if not instruction_path.is_file():
        raise SystemExit(f"instruction file does not exist: {instruction_path}")
    if not Path(args.root).is_dir():
        raise SystemExit(f"repository root does not exist: {args.root}")
    instruction = instruction_path.read_text(encoding="utf-8").strip()
    product_argv = build_product_argv(
        instruction=instruction,
        root=args.root,
        model=args.model,
        base_url=args.base_url,
        task_id=args.task_id,
        source_sha=args.product_source_sha,
        time_budget_seconds=args.time_budget_seconds,
        max_iterations=args.max_iterations,
        output=args.output,
        state_dir=args.state_dir,
        temperature=args.temperature,
    )
    _write_adapter_receipt(Path(args.adapter_receipt), args=args)
    completed = subprocess.run(product_argv, check=False, env=os.environ.copy())
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_product_argv", "main"]
