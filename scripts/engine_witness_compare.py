"""IE-14 witness comparison: ENGINE vs frozen GT-off baseline, ten tasks.

Reads both arms' trajectories and produces the matched per-task table:
reward, calls, actions, exploration (pre-edit actions), raw bytes, GT bytes,
total visible bytes, and (engine arm) all five decision counts + fallback
incidents. Never claims a general efficacy result from ten tasks.

Usage:
    python scripts/engine_witness_compare.py \
        --baseline "C:/Users/Lenovo/Downloads/gt-off-baseline deepseeknew" \
        --engine  <dir-of-engine-tasks> [--tasks fix-code-vulnerability,...] \
        [--out engine_witness.md]
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

DECISION_RE = re.compile(r'decision=\\?"([^"]+)"')
GT_ENGINE_RE = re.compile(r"<result")
FALLBACK_RE = re.compile(r"notice: .*?(fallback|no answer)")

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


def _load_messages(trajectory_path: Path) -> list[dict]:
    try:
        payload = json.loads(trajectory_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(payload, dict):
        return payload.get("messages") or []
    if isinstance(payload, list):
        return payload
    return []


def _arm_metrics(trajectory_path: Path) -> dict:
    """Compute metrics from one arm's trajectory file for a task."""
    msgs = _load_messages(trajectory_path)
    calls = 0
    actions = 0
    raw_bytes = 0
    gt_bytes = 0
    visible_bytes = 0
    decisions: Counter = Counter()
    fallbacks = 0
    pre_edit_actions = 0
    saw_edit = False
    for msg in msgs:
        role = msg.get("role")
        if role == "assistant":
            msg_actions = (msg.get("extra") or {}).get("actions") or []
            if msg_actions:
                calls += 1
                for act in msg_actions:
                    actions += 1
                    command = str(act.get("command") or act.get("cmd") or "")
                    # crude edit heuristic: an action touching a source path
                    # with a write verb is an edit attempt
                    if (
                        re.search(
                            r"\b(cat|printf|echo|sed -i|tee|python|nano|vi)\b",
                            command,
                        )
                        and not saw_edit
                    ):
                        pre_edit_actions += 1
                    if not saw_edit and re.search(r"\b(sed -i|tee|>|>>)\b", command):
                        saw_edit = True
        elif role == "tool":
            content = str(msg.get("content") or "")
            visible_bytes += len(content.encode("utf-8"))
            if GT_ENGINE_RE.search(content):
                gt_bytes += len(content.encode("utf-8"))
                decision = DECISION_RE.search(content)
                if decision:
                    decisions[decision.group(1)] += 1
                if FALLBACK_RE.search(content):
                    fallbacks += 1
            raw_bytes += len(content.encode("utf-8"))
    return {
        "calls": calls,
        "actions": actions,
        "pre_edit_actions": pre_edit_actions,
        "raw_bytes": raw_bytes,
        "gt_bytes": gt_bytes,
        "visible_bytes": visible_bytes,
        "decisions": dict(decisions),
        "fallbacks": fallbacks,
    }


def _baseline_tokens(baseline_dir: Path) -> dict[str, list]:
    """task -> [calls, prompt_tok, comp_tok] from per_task_tokens.json."""
    out: dict[str, list] = {}
    path = baseline_dir / "per_task_tokens.json"
    if not path.exists():
        return out
    for row in json.loads(path.read_text(encoding="utf-8")):
        if len(row) >= 4:
            out[str(row[0])] = [int(row[1]), int(row[2]), int(row[3])]
    return out


def _baseline_reward(baseline_dir: Path, task: str) -> float:
    summary = baseline_dir / "SUMMARY.md"
    if not summary.exists():
        return float("nan")
    for line in summary.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"| {task} "):
            match = re.search(r'"reward":\s*([0-9.]+)', line)
            return float(match.group(1)) if match else float("nan")
    return float("nan")


def compare(baseline_dir: Path, engine_dir: Path, tasks: list[str]) -> dict:
    baseline_tokens = _baseline_tokens(baseline_dir)
    rows: list[dict] = []
    for task in tasks:
        engine_task = engine_dir / f"{task}_trajectory.json"
        base_task = baseline_dir / f"{task}_trajectory.json"
        eng = _arm_metrics(engine_task) if engine_task.exists() else {}
        base = _arm_metrics(base_task) if base_task.exists() else {}
        base_tokens = baseline_tokens.get(task, [0, 0, 0])
        rows.append({
            "task": task,
            "baseline_reward": _baseline_reward(baseline_dir, task),
            "engine_has_trajectory": engine_task.exists(),
            "calls": {"baseline": base.get("calls", 0) or base_tokens[0],
                      "engine": eng.get("calls", 0)},
            "actions": {"baseline": base.get("actions", 0),
                        "engine": eng.get("actions", 0)},
            "pre_edit_actions": {"baseline": base.get("pre_edit_actions", 0),
                                 "engine": eng.get("pre_edit_actions", 0)},
            "raw_bytes": {"baseline": base.get("raw_bytes", 0),
                          "engine": eng.get("raw_bytes", 0)},
            "gt_bytes": {"baseline": base.get("gt_bytes", 0),
                         "engine": eng.get("gt_bytes", 0)},
            "visible_bytes": {"baseline": base.get("visible_bytes", 0),
                              "engine": eng.get("visible_bytes", 0)},
            "decisions": eng.get("decisions", {}),
            "fallbacks": eng.get("fallbacks", 0),
            "baseline_prompt_tok": base_tokens[1],
            "baseline_comp_tok": base_tokens[2],
        })
    return {"tasks": rows}


def render_markdown(result: dict) -> str:
    lines = ["# ENGINE witness — 10-task matched comparison", "",
             "Baseline: Mini-SWE 2.2.8 / DeepSeek V4 Flash / temp 1.0 / step 100 "
             "(frozen; not rerun). Ten tasks only; no general efficacy claim.",
             "", "| task | base rwd | engine rwd | calls B/E | actions B/E | "
                 "pre-edit B/E | raw B/E | gt B/E | visible B/E | decisions (engine) | fallback |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for row in result["tasks"]:
        eng_reward = "?" if not row["engine_has_trajectory"] else "graded"
        lines.append(
            f"| {row['task']} | {row['baseline_reward']} | {eng_reward} "
            f"| {row['calls']['baseline']}/{row['calls']['engine']} "
            f"| {row['actions']['baseline']}/{row['actions']['engine']} "
            f"| {row['pre_edit_actions']['baseline']}/{row['pre_edit_actions']['engine']} "
            f"| {row['raw_bytes']['baseline']}/{row['raw_bytes']['engine']} "
            f"| {row['gt_bytes']['baseline']}/{row['gt_bytes']['engine']} "
            f"| {row['visible_bytes']['baseline']}/{row['visible_bytes']['engine']} "
            f"| {json.dumps(row['decisions'], sort_keys=True)} "
            f"| {row['fallbacks']} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, help="frozen baseline dir")
    parser.add_argument("--engine", required=True, help="engine results dir")
    parser.add_argument("--tasks", default="", help="comma-separated task names")
    parser.add_argument("--out", default="", help="write markdown here")
    args = parser.parse_args()

    tasks = [t.strip() for t in args.tasks.split(",") if t.strip()] or REPAIR_MIX_TASKS
    result = compare(Path(args.baseline), Path(args.engine), tasks)
    markdown = render_markdown(result)
    print(markdown)
    if args.out:
        Path(args.out).write_text(markdown, encoding="utf-8")
    print(json.dumps(result, indent=2)[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
