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

# Pier starts Harbor's outer timer before the task-container runner attaches,
# and the runner needs time after the deadline to close the session and publish
# receipts. Both ends are now measured rather than estimated: on run
# 34305004976 the step began at 02:57:14 and the journal opened at 02:58:54
# (100s of pre-run setup), and the session closed at 03:38:39 with the step
# ending at 03:39:47 (68s of finalization). 168s observed, so 240 keeps a 72s
# margin on both ends together.
#
# The reserve is subtracted from the benchmark's own 5400s, so every second held
# back here is a second the agent does not get and the leaderboard's agent did.
# Seven tasks in run 34312022821 died exactly on our line rather than the
# benchmark's. Size it from the measurement, not from a round number.
SUPERVISOR_GRACE_SECONDS = 240

# The benchmark's 5400s bounds the AGENT. GT spends measured, attributable time
# before the agent's first action that a stock mini-swe-agent run never spends:
# it builds a code graph, and it makes one planning call. Measured on run
# 34360947973, task claude-code-by-agents-recursive-delegation:
#
#   job start -> first GT event        247s   image, repo checkout, graph build
#   planning call                      702s   22,004 completion tokens
#   ------------------------------------------
#   before the agent's first step      949s   18% of the 5,160s it was given
#
# Charging that to the agent's clock measures our setup instead of the agent's
# reasoning, and it is not a hypothetical: all seven tasks in that run reached
# the deadline with the agent still working. So the setup is paid for openly.
#
# This is a DEVIATION from the benchmark's declared budget. It is added after
# any stage cap, reported in the receipt as its own field beside the untouched
# benchmark base, and surfaced in the attestation, so no number produced under
# it can be mistaken for a stock 5400s result or compared to one without the
# difference being visible. Token and step counts remain directly comparable;
# wall-clock does not.
GT_OVERHEAD_EXTENSION_SECONDS = 1500

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
    overhead_extension_sec: float = 0.0,
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
    if overhead_extension_sec < 0:
        raise ValueError("overhead extension must not be negative")
    # After the cap, deliberately. The cap keeps every task on the same
    # benchmark rail; the extension is GT's own setup cost and is the same for
    # each, so applying it afterwards keeps the tasks comparable to one another
    # while making the deviation from the benchmark a single visible number.
    benchmark_budget = resolved
    resolved += float(overhead_extension_sec)
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
        # What the benchmark's own configuration allows, untouched.
        "benchmark_budget_sec": benchmark_budget,
        # What GT added to it, and why, in one place a reader cannot miss.
        "gt_overhead_extension_sec": float(overhead_extension_sec),
        "gt_overhead_extension_reason": (
            "graph build and one planning call precede the agent's first step"
        ),
        "deviates_from_benchmark_budget": bool(overhead_extension_sec),
        "execution_budget_sec": resolved,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-config", type=Path, required=True)
    parser.add_argument("--multiplier", type=float, required=True)
    parser.add_argument("--max-timeout-sec", type=float)
    parser.add_argument(
        "--overhead-extension-sec",
        type=float,
        default=0.0,
        help=(
            "seconds added on top of the benchmark budget to pay for GT's own "
            "pre-agent work; reported in the receipt as a deviation"
        ),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    receipt = resolve_budget(
        args.task_config,
        multiplier=args.multiplier,
        max_timeout_sec=args.max_timeout_sec,
        overhead_extension_sec=args.overhead_extension_sec,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(receipt["execution_budget_sec"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
