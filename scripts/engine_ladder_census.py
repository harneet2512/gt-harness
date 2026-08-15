"""ENGINE causal ladder census (W5).

For each delivered GT fact in a trajectory, compute its receipt-ladder position:
- L1 delivered: the fact was emitted in an action's observation.
- L2 referenced: a LATER assistant message mentions the fact's anchor.
- L3 acted: a LATER command targets the anchor.

A fact that never reaches L2/L3 is inert. This is the causal endpoint for the
round-3 smoke — not token deltas (temp-1.0 noise) and not solve rate.

Usage:
    python scripts/engine_ladder_census.py <flat-trajectory-dir> [--tasks ...]
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

FACT_RE = re.compile(r'<fact owner="([^"]+)"[^>]*>(.*?)</fact>', re.S)

TEN = [
    "fix-code-vulnerability", "portfolio-optimization", "modernize-scientific-stack",
    "headless-terminal", "llm-inference-batching-scheduler", "break-filter-js-from-html",
    "write-compressor", "gpt2-codegolf", "schemelike-metacircular-eval",
    "cobol-modernization",
]


def _anchors(content_json: str) -> tuple[str, ...]:
    """Extract stable anchors a later action could target (paths + symbols).

    Reads the rendered fact's content JSON (file/path/target/literal/symbol/
    name/subjects) and the render's ``anchors:`` line."""
    anchors: list[str] = []
    for m in re.finditer(
        r'"(?:file|path|target|name|subject|literal|symbol)":\s*"([^"]+)"',
        content_json,
    ):
        anchors.append(m.group(1))
    for m in re.finditer(r'"(?:subjects|requirements)":\s*\[([^\]]*)\]', content_json):
        anchors.extend(
            a.strip().strip('"') for a in m.group(1).split(",") if a.strip()
        )
    for m in re.finditer(r"anchors:\s*([^\n<]+)", content_json):
        anchors.extend(a.strip() for a in m.group(1).split() if a.strip())
    return tuple(dict.fromkeys(a for a in anchors if a))


def _ladder(msgs: list[dict]) -> dict[str, dict[str, int]]:
    """Per-feature ladder: delivered / referenced / acted.

    A delivered fact stays pending until the end of the episode (or until it is
    acted on), so a reference or action several turns later still counts.
    Reference and act are counted once per fact each; reference is not a
    prerequisite for act.
    """
    census: dict[str, dict[str, int]] = defaultdict(
        lambda: {"delivered": 0, "referenced": 0, "acted": 0,
                 "first_acted_index": -1}
    )
    pending: list[dict] = []  # {"feature", "anchors", "referenced", "acted"}
    action_seq = 0
    for msg in msgs:
        role = msg.get("role")
        content = str(msg.get("content") or "")
        if role == "tool":
            for fm in FACT_RE.finditer(content):
                feature = fm.group(1)
                anchors = _anchors(fm.group(2))
                census[feature]["delivered"] += 1
                if anchors:
                    pending.append({
                        "feature": feature, "anchors": anchors,
                        "referenced": False, "acted": False,
                    })
        elif role == "assistant":
            actions = (msg.get("extra") or {}).get("actions") or []
            commands = [str(a.get("command") or a.get("cmd") or "") for a in actions]
            action_seq += len(commands)
            text = content
            for entry in pending:
                feature = entry["feature"]
                anchors = entry["anchors"]
                if not entry["referenced"] and any(
                    a and a in text for a in anchors
                ):
                    census[feature]["referenced"] += 1
                    entry["referenced"] = True
                if not entry["acted"] and any(
                    a and any(a in c for c in commands) for a in anchors
                ):
                    census[feature]["acted"] += 1
                    entry["acted"] = True
                    if census[feature]["first_acted_index"] < 0:
                        census[feature]["first_acted_index"] = action_seq
    return dict(census)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("flat_dir")
    parser.add_argument("--tasks", default="")
    args = parser.parse_args()
    root = Path(args.flat_dir)
    tasks = [t.strip() for t in args.tasks.split(",") if t.strip()] or TEN
    combined: dict[str, dict[str, int]] = defaultdict(
        lambda: {"delivered": 0, "referenced": 0, "acted": 0}
    )
    print(f"| task | facts delivered | L2 referenced | L3 acted | inert |")
    print("|---|---|---|---|---|")
    for task in tasks:
        tj = root / f"{task}_trajectory.json"
        if not tj.exists():
            print(f"| {task} | - | - | - | - |")
            continue
        msgs = json.loads(tj.read_text(encoding="utf-8"))["messages"]
        ladder = _ladder(msgs)
        total = sum(v["delivered"] for v in ladder.values())
        ref = sum(v["referenced"] for v in ladder.values())
        acted = sum(v["acted"] for v in ladder.values())
        print(f"| {task} | {total} | {ref} | {acted} | {max(total - ref - acted, 0)} |")
        for feature, v in ladder.items():
            combined[feature]["delivered"] += v["delivered"]
            combined[feature]["referenced"] += v["referenced"]
            combined[feature]["acted"] += v["acted"]
    print()
    print(f"| feature | delivered | L2 referenced | L3 acted |")
    print("|---|---|---|---|")
    for feature, v in sorted(combined.items()):
        print(f"| {feature} | {v['delivered']} | {v['referenced']} | {v['acted']} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
