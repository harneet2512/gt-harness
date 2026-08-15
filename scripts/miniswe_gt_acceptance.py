#!/usr/bin/env python3
"""Action-consistency (acceptance) audit for a Mini-SWE GT-on run.

For every GT evidence delivery, classify whether the model's NEXT action used
it:

- localization      -> the ranked target file was viewed/grepped next
- covering/syntax   -> the edited surface was touched next
- submit_refusal    -> a verification ran before the next submit
- recovery_steer    -> the next action changed (not an identical repeat)

A feature that delivers but is never consumed is overhead, not value. This is
the acceptance gate: ship only what the model obeys.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_VERIFY_RE = re.compile(
    r"(?i)(pytest|unittest|npm\s+test|cargo\s+test|go\s+test|check|verify|compile)"
)
_ROW_RE = re.compile(r"(?m)^([A-Za-z0-9_./-]+\.py):\d+:[A-Za-z_]\w*\s*$")


def _load_events(run_dir: Path) -> list[dict]:
    rows: list[dict] = []
    for sub in (run_dir / "agent" / "gt-state").rglob("events.jsonl"):
        for line in sub.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _action_map(msgs: list[dict]) -> dict[int, dict]:
    """action_index -> {tool_idx, next_assistant_idx}."""
    out: dict[int, dict] = {}
    counter = 0
    for i, msg in enumerate(msgs):
        if msg.get("role") != "assistant":
            continue
        actions = (msg.get("extra") or {}).get("actions") or []
        for j, _a in enumerate(actions):
            counter += 1
            tool_idx = i + 1 + j
            nxt = next(
                (k for k in range(tool_idx + 1, len(msgs))
                 if msgs[k].get("role") == "assistant"),
                None,
            )
            out[counter] = {"tool_idx": tool_idx, "next_assistant_idx": nxt}
    return out


def _actions_of(msg: dict | None) -> list[str]:
    if not msg:
        return []
    return [str(a.get("command") or "") for a in (msg.get("extra") or {}).get("actions", [])]


def audit(run_dir: Path) -> dict[str, dict[str, int]]:
    run_dir = Path(run_dir)
    stats: dict[str, dict[str, int]] = {}
    for task_dir in sorted(run_dir.iterdir()):
        if not task_dir.is_dir():
            continue
        traj_file = task_dir / "agent" / "miniswe_trajectory.json"
        if not traj_file.exists():
            continue
        events = _load_events(task_dir)
        if not events:
            continue
        raw = json.loads(traj_file.read_text(encoding="utf-8", errors="replace"))
        msgs = raw.get("messages", [])
        action_map = _action_map(msgs)
        def _window_from(msg_list: list[dict], start_idx: int | None,
                         limit: int = 6) -> list[str]:
            window: list[str] = []
            k = start_idx
            while k is not None and len(window) < limit:
                window.extend(_actions_of(msg_list[k] if k < len(msg_list) else None))
                k = next(
                    (j for j in range(k + 1, len(msg_list))
                     if msg_list[j].get("role") == "assistant"),
                    None,
                )
            return window

        for row in events:
            ev_type = row.get("evidence_type")
            if row.get("event") == "evidence_delivery" and ev_type == "localization":
                target = str(row.get("target") or "")
                if not target:
                    # fall back to the spliced <gt-facts> block in the tool message
                    info = action_map.get(int(row.get("action_index") or 0))
                    if info and info["tool_idx"] < len(msgs):
                        rows = _ROW_RE.findall(str(msgs[info["tool_idx"]].get("content") or ""))
                        target = rows[0] if rows else ""
                act_idx = int(row.get("action_index") or 0)
                # task-start delivery (action 0): the model's FIRST actions
                # should touch the ranked target; else the next assistant window
                if act_idx == 0:
                    start_idx = next(
                        (i for i, m in enumerate(msgs) if m.get("role") == "assistant"),
                        None,
                    )
                else:
                    info = action_map.get(act_idx)
                    start_idx = info["next_assistant_idx"] if info else None
                acted = bool(target and any(
                    target in c or target.split("/")[-1] in c
                    for c in _window_from(msgs, start_idx)
                ))
                _bump(stats, "localization", acted)
            elif row.get("event") == "submit_refusal":
                # the refusal is a role=user neutral directive; the model
                # ACCEPTS it when its next action verifies before resubmitting
                refusal_idx = next(
                    (i for i, m in enumerate(msgs)
                     if m.get("role") == "user"
                     and "Submission not executed" in str(m.get("content") or "")),
                    None,
                )
                nxt_msg = None
                if refusal_idx is not None:
                    nxt_msg = next(
                        (m for m in msgs[refusal_idx + 1:]
                         if m.get("role") == "assistant"),
                        None,
                    )
                acts = _actions_of(nxt_msg)
                verified = any(_VERIFY_RE.search(c) for c in acts)
                _bump(stats, "submit_refusal", verified)
    return stats


def _bump(stats: dict[str, dict[str, int]], key: str, ok: bool) -> None:
    entry = stats.setdefault(key, {"delivered": 0, "accepted": 0})
    entry["delivered"] += 1
    if ok:
        entry["accepted"] += 1


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args(argv)
    stats = audit(args.run_dir)
    print(f"=== acceptance audit: {args.run_dir.name} ===")
    for key, entry in sorted(stats.items()):
        d, a = entry["delivered"], entry["accepted"]
        pct = 100 * a / d if d else 0
        print(f"  {key:18s} delivered={d:3d} accepted={a:3d} ({pct:5.1f}%)")
    if not stats:
        print("  (no evidence deliveries found)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
