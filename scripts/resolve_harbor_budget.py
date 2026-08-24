#!/usr/bin/env python3
"""Resolve Harbor's task-owned agent timeout before a custom agent starts.

Harbor 0.20 wraps ``BaseAgent.run`` in this deadline but does not expose it in
``AgentContext``.  The paid workflow reads the same exported ``task.toml`` and
passes the resolved value as an agent kwarg.  Missing or ambiguous input fails
closed; this script never invents a timeout or changes Harbor's configured one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tomllib
from pathlib import Path
from typing import Any

# Harbor owns the real task ceiling. GT stops new work this much earlier so
# trajectory/receipt finalization can complete without stealing material solve time.
SUPERVISOR_GRACE_SECONDS = 90


def resolve_budget(
    task_config: Path,
    *,
    multiplier: float,
    max_timeout_sec: float | None = None,
) -> dict[str, Any]:
    raw = task_config.read_bytes()
    payload = tomllib.loads(raw.decode("utf-8"))
    agent = payload.get("agent") or {}
    base = agent.get("timeout_sec") if isinstance(agent, dict) else None
    if not isinstance(base, (int, float)) or isinstance(base, bool) or base <= 0:
        raise ValueError("task.toml must define a positive agent.timeout_sec")
    if multiplier <= 0:
        raise ValueError("timeout multiplier must be positive")
    resolved = float(base) * float(multiplier)
    if max_timeout_sec is not None:
        if max_timeout_sec <= 0:
            raise ValueError("maximum timeout must be positive")
        resolved = min(resolved, float(max_timeout_sec))
    return {
        "schema": "harbor-agent-budget-v1",
        "source": "task.toml:[agent].timeout_sec",
        "task_config": task_config.as_posix(),
        "task_config_sha256": hashlib.sha256(raw).hexdigest(),
        "base_timeout_sec": float(base),
        "timeout_multiplier": float(multiplier),
        "max_timeout_sec": (
            None if max_timeout_sec is None else float(max_timeout_sec)
        ),
        "execution_budget_sec": resolved,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-config", type=Path, required=True)
    parser.add_argument("--multiplier", type=float, required=True)
    parser.add_argument("--max-timeout-sec", type=float)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    receipt = resolve_budget(
        args.task_config,
        multiplier=args.multiplier,
        max_timeout_sec=args.max_timeout_sec,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(receipt["execution_budget_sec"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
