"""ENGINE vs frozen baseline — per-task token, call, action, and cost deltas.

Baseline tokens come from per_task_tokens.json ([task, calls, prompt_tok,
comp_tok, ...]). ENGINE tokens are summed from the trajectory's per-call
usage (prompt_tokens / completion_tokens / cache hit/miss). Deltas are
descriptive only; no efficacy claim from ten tasks.

Usage:
    python scripts/engine_delta_compare.py \
        --baseline "C:/Users/Lenovo/Downloads/gt-off-baseline deepseeknew" \
        --engine  <flat-engine-trajectory-dir> [--out engine_delta.md]
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

REPAIR_MIX_TASKS = [
    "extract-elf", "mcmc-sampling-stan", "prove-plus-comm", "qemu-alpine-ssh",
    "regex-chess", "sanitize-git-repo", "torch-tensor-parallelism", "video-processing",
    "winning-avg-corewars", "write-compressor", "headless-terminal",
    "portfolio-optimization", "schemelike-metacircular-eval", "cobol-modernization",
    "llm-inference-batching-scheduler", "fix-code-vulnerability",
    "feal-linear-cryptanalysis", "count-dataset-tokens", "largest-eigenval",
    "torch-pipeline-parallelism",
]
TEN_SMOKE_TASKS = REPAIR_MIX_TASKS  # compatibility alias for older callers

# DeepSeek V4 Flash pricing (per 1M tokens).
PRICE_INPUT_HIT = 0.0028
PRICE_INPUT_MISS = 0.14
PRICE_OUTPUT = 0.28


def _load_messages(path: Path) -> list[dict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(payload, dict):
        return payload.get("messages") or []
    return payload if isinstance(payload, list) else []


def _baseline_tokens(baseline_dir: Path) -> dict[str, list]:
    out: dict[str, list] = {}
    path = baseline_dir / "per_task_tokens.json"
    if not path.exists():
        return out
    for row in json.loads(path.read_text(encoding="utf-8")):
        if len(row) >= 4:
            out[str(row[0])] = [int(row[1]), int(row[2]), int(row[3])]
    return out


def _engine_usage(trajectory_path: Path) -> dict:
    """Sum usage over assistant messages from an engine trajectory."""
    stats = {
        "calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
        "cache_hit": 0, "cache_miss": 0, "actions": 0, "submits": 0,
    }
    for msg in _load_messages(trajectory_path):
        if msg.get("role") != "assistant":
            continue
        extra = msg.get("extra") or {}
        actions = extra.get("actions") or []
        if actions:
            stats["calls"] += 1
            stats["actions"] += len(actions)
            for act in actions:
                cmd = str(act.get("command") or act.get("cmd") or "")
                if re.search(r"(^|[\s;])git .*(push|commit)|submit", cmd):
                    stats["submits"] += 1
        usage = (extra.get("response") or {}).get("usage") or {}
        stats["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
        stats["completion_tokens"] += int(usage.get("completion_tokens") or 0)
        stats["cache_hit"] += int(usage.get("prompt_cache_hit_tokens") or 0)
        stats["cache_miss"] += int(usage.get("prompt_cache_miss_tokens") or 0)
    return stats


def _cost(miss: int, hit: int, comp: int) -> float:
    return (
        miss * PRICE_INPUT_MISS
        + hit * PRICE_INPUT_HIT
        + comp * PRICE_OUTPUT
    ) / 1_000_000


def _pct(delta: float, base: float) -> str:
    if not base:
        return "n/a"
    return f"{delta / base * 100:+.1f}%"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--engine", required=True)
    parser.add_argument("--tasks", default="")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    base_dir = Path(args.baseline)
    engine_dir = Path(args.engine)
    tasks = [t.strip() for t in args.tasks.split(",") if t.strip()] or REPAIR_MIX_TASKS

    lines = [
        "# ENGINE vs baseline — token / call / action / cost deltas",
        "",
        "Both arms summed identically from per-call trajectory usage. "
        "Baseline: Mini-SWE 2.2.8 / DeepSeek V4 Flash / temp 1.0 / step 100 "
        "(frozen). ENGINE run `30736459512`. Deltas are descriptive; no "
        "efficacy claim.",
        "",
        "| task | calls B→E | prompt tok B→E | comp tok B→E | total B→E | Δtotal | "
        "actions B→E | est. cost B→E |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for task in tasks:
        base_tj = base_dir / f"{task}_trajectory.json"
        engine_tj = engine_dir / f"{task}_trajectory.json"
        base = _engine_usage(base_tj) if base_tj.exists() else {}
        eng = _engine_usage(engine_tj) if engine_tj.exists() else {}
        b_total = base.get("prompt_tokens", 0) + base.get("completion_tokens", 0)
        e_total = eng.get("prompt_tokens", 0) + eng.get("completion_tokens", 0)
        delta = e_total - b_total
        b_cost = _cost(base.get("cache_miss", 0), base.get("cache_hit", 0),
                       base.get("completion_tokens", 0))
        e_cost = _cost(eng.get("cache_miss", 0), eng.get("cache_hit", 0),
                       eng.get("completion_tokens", 0))
        lines.append(
            f"| {task} | {base.get('calls', 0)}→{eng.get('calls', 0)} "
            f"| {base.get('prompt_tokens', 0):,}→{eng.get('prompt_tokens', 0):,} "
            f"| {base.get('completion_tokens', 0):,}→{eng.get('completion_tokens', 0):,} "
            f"| {b_total:,}→{e_total:,} "
            f"| {_pct(delta, b_total)} | "
            f"{base.get('actions', 0)}→{eng.get('actions', 0)} "
            f"| ${b_cost:.2f}→${e_cost:.2f} |"
        )
    body = "\n".join(lines) + "\n"
    print(body)
    if args.out:
        Path(args.out).write_text(body, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
