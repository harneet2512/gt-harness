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

# Pier starts Harbor's outer timer before the task-container runner attaches.
# The live gate observed ~115 seconds of pre-run setup. Reserve five minutes so
# the inner supervisor can stop, close the session, and publish receipts before
# Pier cancels the entire process.
SUPERVISOR_GRACE_SECONDS = 300
TASK_CONFIG_IDENTITY = "sha256_canonical_lf_v1"


def canonical_task_config_bytes(raw: bytes) -> bytes:
    """Return the Git-blob newline form used by the pinned task manifest.

    Git may materialize text files with CRLF in a Windows checkout even though
    the immutable dataset blob uses LF.  Task configuration identity must not
    depend on the checkout platform.  Bare CR bytes are rejected because they
    are neither the canonical Git representation nor an unambiguous CRLF
    checkout transformation.
    """
    without_crlf = raw.replace(b"\r\n", b"\n")
    if b"\r" in without_crlf:
        raise ValueError("task.toml contains non-canonical bare CR bytes")
    return without_crlf


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
        "task_config_identity": TASK_CONFIG_IDENTITY,
        "task_config": task_config.as_posix(),
        "task_config_sha256": hashlib.sha256(
            canonical_task_config_bytes(raw)
        ).hexdigest(),
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
