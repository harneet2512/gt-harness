#!/usr/bin/env python3
"""Fail-safe, merge-independent DeepSWE artifact aggregation.

This is intentionally conservative: an artifact is counted as graded only when
its verifier reward is an explicit numeric 0/1.  Missing or malformed artifacts
remain visible as incomplete instead of being silently treated as failures.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path


def rows(root: Path) -> list[dict]:
    out: list[dict] = []
    for path in sorted(root.rglob("result.json")):
        # Pier stores one aggregate result alongside one task result.  A task
        # result directory always contains the benchmark's ``__`` separator.
        if "__" not in str(path.parent):
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            out.append({"task": path.parent.name.split("__", 1)[0],
                        "reward": None, "error": f"invalid_json:{type(exc).__name__}"})
            continue
        verifier = raw.get("verifier_result")
        verifier = verifier if isinstance(verifier, dict) else {}
        rewards = verifier.get("rewards")
        rewards = rewards if isinstance(rewards, dict) else {}
        reward = rewards.get("reward")
        if isinstance(reward, bool):
            reward = float(reward)
        elif isinstance(reward, (int, float)) and reward in (0, 1):
            reward = float(reward)
        else:
            reward = None
        agent = raw.get("agent_result")
        agent = agent if isinstance(agent, dict) else {}
        metadata = agent.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        stats = agent.get("n_input_tokens"), agent.get("n_cache_tokens"), agent.get("n_output_tokens")
        started, finished = raw.get("started_at"), raw.get("finished_at")
        wall = None
        try:
            wall = (datetime.fromisoformat(finished.replace("Z", "+00:00")) -
                    datetime.fromisoformat(started.replace("Z", "+00:00"))).total_seconds()
        except (AttributeError, TypeError, ValueError):
            pass
        out.append({"task": path.parent.name.split("__", 1)[0],
                    "reward": reward,
                    "exit": metadata.get("exit_status"),
                    "exception": bool(raw.get("exception_info")),
                    "agent_steps": raw.get("n_agent_steps", agent.get("n_agent_steps")),
                    "input_tokens": stats[0], "cache_tokens": stats[1],
                    "output_tokens": stats[2], "cost_usd": agent.get("cost_usd"),
                    "wall_time_sec": wall,
                    "model": (raw.get("agent_info") or {}).get("model_info", {}).get("name"),
                    "provider": (raw.get("agent_info") or {}).get("model_info", {}).get("provider")})
    return out


def main() -> int:
    root = Path(os.environ.get("ARTIFACT_ROOT", "tasks"))
    data = rows(root)
    graded = [r for r in data if r["reward"] is not None]
    solved = [r for r in graded if r["reward"] == 1.0]
    payload = {
        "schema": "gt.deepswe.partial.v1",
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "returned": len(data),
        "graded": len(graded),
        "ungraded": len(data) - len(graded),
        "solved": len(solved),
        "unsolved": len(graded) - len(solved),
        "metrics": {k: sum((r.get(k) or 0) for r in data)
                    for k in ("agent_steps", "input_tokens", "cache_tokens", "output_tokens", "wall_time_sec")},
        "cost_usd_observed": any(r.get("cost_usd") is not None for r in data),
        "rows": sorted(data, key=lambda r: r["task"]),
    }
    output = Path(os.environ.get("OUTPUT", "gtoff_partial_summary.json"))
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print("## DeepSWE GT-off partial results")
    print(f"returned={payload['returned']} graded={payload['graded']} "
          f"solved={payload['solved']} unsolved={payload['unsolved']} "
          f"ungraded={payload['ungraded']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
