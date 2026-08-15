"""Cross-run engine audit: which of the 17 DIRECT features worked, + all-run delta.

Reads each round's flat trajectories (facts + ladder) and merged.json (rewards),
and emits:
  1. per-feature worked/delivered/acted across every round
  2. per-task solve + token/call delta vs baseline and round-over-round
  3. the 17-feature worked matrix

Usage:
    python scripts/engine_round_audit.py
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
OP = Path(r"D:\tmp\opencode")
BASE = Path(r"C:\Users\Lenovo\Downloads\gt-off-baseline deepseeknew")

TEN = [
    "fix-code-vulnerability", "portfolio-optimization", "modernize-scientific-stack",
    "headless-terminal", "llm-inference-batching-scheduler", "break-filter-js-from-html",
    "write-compressor", "gpt2-codegolf", "schemelike-metacircular-eval",
    "cobol-modernization",
]

FEATURES = [
    "obligations", "localization", "def_partition", "syntax_result",
    "covering_red", "recovery", "signature_delta", "newfile_precedent",
    "submit_refusal",  # 9 FACT
    "GT_EDIT_CHECK", "GT_PATCH_DELTA", "GT_LOC_RESLOT", "GT_SS_SUBMIT_RED",
    "GT_HYPOTHESIS", "GT_CHANGE_SURFACE", "GT_CERT_DELIVERY",  # 7 CAP_OWNER
    "caller_contract",  # REMOVE by disposition
]

FACT_RE = re.compile(r'<fact owner="([^"]+)"[^>]*>(.*?)</fact>', re.S)

# round label -> (flat_dir, merged_glob)
ROUNDS = {
    "r2": ("engine_r2_flat", "engine_r2"),
    "r3": ("engine_r3_flat", "engine_r3"),
    "r4": ("engine_r4_flat", "engine_r4"),
    "r5": ("engine_r5_flat", "engine_r5"),
    "r6": ("engine_r6_flat", "engine_r6"),
    "r7": ("engine_r7_flat", "engine_r7"),
    "r8": ("engine_r8_flat", "engine_r8"),
}


def _facts_in_trajectory(tj: Path) -> dict[str, dict]:
    d = json.loads(tj.read_text(encoding="utf-8"))
    out: dict[str, dict] = defaultdict(
        lambda: {"delivered": 0, "empty_evidence": 0, "referenced": 0, "acted": 0,
                 "first_acted_index": -1}
    )
    pending: list[dict] = []
    action_seq = 0
    for m in d.get("messages", []):
        role = m.get("role")
        content = str(m.get("content") or "")
        if role == "tool":
            for fm in FACT_RE.finditer(content):
                owner, body = fm.group(1), fm.group(2)
                out[owner]["delivered"] += 1
                if '"evidence": ""' in body:
                    out[owner]["empty_evidence"] += 1
                anchors = tuple(
                    dict.fromkeys(
                        a for a in re.findall(
                            r'"(?:file|path|target|name|subject|literal|symbol)":\s*"([^"]+)"',
                            body,
                        )
                        if a
                    )
                )
                if anchors:
                    pending.append({"feature": owner, "anchors": anchors,
                                    "referenced": False, "acted": False})
        elif role == "assistant":
            actions = (m.get("extra") or {}).get("actions") or []
            commands = [str(a.get("command") or a.get("cmd") or "") for a in actions]
            action_seq += len(commands)
            text = content
            for entry in pending:
                f = entry["feature"]
                if not entry["referenced"] and any(a and a in text for a in entry["anchors"]):
                    out[f]["referenced"] += 1
                    entry["referenced"] = True
                if not entry["acted"] and any(a and any(a in c for c in commands) for a in entry["anchors"]):
                    out[f]["acted"] += 1
                    entry["acted"] = True
                    if out[f]["first_acted_index"] < 0:
                        out[f]["first_acted_index"] = action_seq
    return dict(out)


def _rewards_for(round_dir: str) -> dict[str, dict]:
    candidates = list((OP / round_dir).glob("*-MERGED/merged.json"))
    if not candidates:
        candidates = [OP / round_dir / "merged.json"]
    rewards: dict[str, dict] = {}
    if candidates and candidates[0].exists():
        d = json.loads(candidates[0].read_text(encoding="utf-8"))
        for t in d.get("trial_results") or []:
            name = (t.get("task_name") or t.get("trial_name", "?"))
            name = str(name).split("__")[0]
            rw = (t.get("verifier_result") or {}).get("rewards") or {}
            val = next((v for v in rw.values() if isinstance(v, (int, float))), None)
            exc = (t.get("exception_info") or {})
            rewards[name] = {"reward": val, "error": bool(exc)}
        if rewards:
            return rewards
    # fallback: per-trial result.json under the round dir
    for rp in (OP / round_dir).rglob("*/*/result.json"):
        try:
            d = json.loads(rp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        v = d.get("verifier_result") or {}
        rw = v.get("rewards") if isinstance(v, dict) else None
        if not isinstance(rw, dict):
            continue
        val = next((x for x in rw.values() if isinstance(x, (int, float))), None)
        name = str(d.get("task_name") or d.get("trial_name", "")).split("__")[0]
        if name and val is not None:
            rewards[name] = {"reward": val, "error": bool(d.get("exception_info"))}
    return rewards


def _traj_bytes(tj: Path) -> dict:
    d = json.loads(tj.read_text(encoding="utf-8"))
    msgs = d.get("messages", [])
    total = 0
    for m in msgs:
        u = (((m.get("extra") or {}).get("response") or {}).get("usage") or {})
        total += u.get("total_tokens") or 0
    return {"total_tokens": total, "n_msgs": len(msgs)}


def main() -> int:
    # 1. baseline tokens (columns: task, calls, p_bytes, c_bytes, ...)
    base_tokens: dict[str, int] = {}
    pt = BASE / "per_task_tokens.json"
    if pt.exists():
        for row in json.loads(pt.read_text(encoding="utf-8")):
            if len(row) >= 5:
                base_tokens[str(row[0])] = int(row[3]) + int(row[4])  # p+c bytes

    print("=" * 78)
    print("A. WHICH OF THE 17 FEATURES WORKED (delivered >= 1 usable fact), per round")
    print("=" * 78)
    print(f"{'feature':<22}" + "".join(f"{r:>7}" for r in ROUNDS))
    per_round_totals = {}
    for r, (flat, _) in ROUNDS.items():
        flat_dir = OP / flat
        if not flat_dir.exists():
            continue
        agg: dict[str, dict] = defaultdict(
            lambda: {"delivered": 0, "empty": 0, "acted": 0})
        for tj in flat_dir.glob("*_trajectory.json"):
            for f, v in _facts_in_trajectory(tj).items():
                agg[f]["delivered"] += v["delivered"]
                agg[f]["empty"] += v["empty_evidence"]
                agg[f]["acted"] += v["acted"]
        per_round_totals[r] = agg

    # FACT features worked marker
    for f in FEATURES[:10]:
        cells = []
        for r in ROUNDS:
            agg = per_round_totals.get(r, {})
            v = agg.get(f)
            if v is None:
                cells.append("     -")
            else:
                mark = "YES" if v["delivered"] > 0 and v["empty"] == 0 else (
                    "part" if v["delivered"] > 0 else "no")
                cells.append(f"{mark:>7}")
        print(f"{f:<22}" + "".join(cells))

    # 2. per-round delivery totals (facts, empty, acted)
    print()
    print("B. FACT DELIVERY TOTALS per round (usable facts / empty-payload / acted)")
    print(f"{'round':<8}{'tasks':>6}{'facts':>8}{'empty':>8}{'acted':>8}{'act%':>7}")
    for r, agg in per_round_totals.items():
        facts = sum(v["delivered"] for v in agg.values())
        empty = sum(v["empty"] for v in agg.values())
        acted = sum(v["acted"] for v in agg.values())
        n_tasks = len(list((OP / ROUNDS[r][0]).glob("*_trajectory.json")))
        print(f"{r:<8}{n_tasks:>6}"
              f"{facts:>8}{empty:>8}{acted:>8}{100*acted//max(facts,1):>6}%")

    # 3. token delta per round, all measured identically (usage.total_tokens
    # from the trajectories). Baseline is in a different unit (bytes) so it is
    # NOT directly comparable; round-over-round is apples-to-apples.
    print()
    print("C. TOTAL_TOKENS per round (all from usage.total_tokens; baseline is bytes, not comparable)")
    print(f"{'task':<30}" + "".join(f"{r:>10}" for r in ROUNDS))
    for task in TEN:
        row = f"{task:<30}"
        for r, (flat, _) in ROUNDS.items():
            tj = OP / flat / f"{task}_trajectory.json"
            if not tj.exists():
                row += f"{'-':>10}"
                continue
            row += f"{_traj_bytes(tj)['total_tokens']:>10,}"
        print(row)

    print()
    print("C2. TOTAL_TOKENS ROUND-OVER-ROUND DELTA % (identical measurement)")
    print(f"{'task':<30}" + "".join(f"{r:>8}" for r in ROUNDS))
    for task in TEN:
        row = f"{task:<30}"
        prev = None
        for r, (flat, _) in ROUNDS.items():
            tj = OP / flat / f"{task}_trajectory.json"
            if not tj.exists():
                row += f"{'-':>8}"
                prev = None
                continue
            cur = _traj_bytes(tj)["total_tokens"]
            if prev:
                row += f"{100*(cur-prev)/prev:+.0f}%".rjust(8)
            else:
                row += f"{'-':>8}"
            prev = cur
        print(row)

    # 4. solved per round
    print()
    print("D. SOLVED (reward 1.0) per task per round")
    print(f"{'task':<30}" + "".join(f"{r:>7}" for r in ROUNDS))
    for task in TEN:
        row = f"{task:<30}"
        for r, (flat, rd) in ROUNDS.items():
            rw = _rewards_for(rd).get(task, {})
            val = rw.get("reward")
            if val is None:
                row += f"{'-':>7}"
            else:
                row += f"{'Y' if val >= 1 else 'N':>7}"
        print(row)

    # 5. efficiency guardrail: actions + harness-probe actions per task
    # (Gap-3). The r8 blowup was the model auditing gt_engine/ source; a run
    # with high harness-probe actions is the signature. Flag > 2x the median
    # action count across rounds for that task.
    print()
    print("E. EFFICIENCY — actions per task, r8 harness-probe actions, flag")
    print(f"{'task':<30}{'r7_acts':>8}{'r8_acts':>8}{'r8_probe':>9}{'flag':>8}")

    def _actions(flat: str, task: str) -> int:
        tj = OP / flat / f"{task}_trajectory.json"
        if not tj.exists():
            return 0
        d = json.loads(tj.read_text(encoding="utf-8"))
        return sum(
            len((m.get("extra") or {}).get("actions") or [])
            for m in d.get("messages", []) if m.get("role") == "assistant"
        )

    def _probes(flat: str, task: str) -> int:
        tj = OP / flat / f"{task}_trajectory.json"
        if not tj.exists():
            return 0
        d = json.loads(tj.read_text(encoding="utf-8"))
        return sum(
            1
            for m in d.get("messages", []) if m.get("role") == "assistant"
            for a in (m.get("extra") or {}).get("actions") or []
            if "gt_engine" in str(a.get("command") or "")
            or "site-packages/gt_engine" in str(a.get("command") or "")
        )

    for task in TEN:
        a7, a8 = _actions("engine_r7_flat", task), _actions("engine_r8_flat", task)
        pr8 = _probes("engine_r8_flat", task)
        flag = "NOISE" if a8 >= 80 or pr8 >= 20 else ""
        print(f"{task:<30}{a7:>8}{a8:>8}{pr8:>9}{flag:>8}")

    # 6. internal-ID leak guardrail (Gap-1): scan the LATEST round's rendered
    # facts for obl-/pred- IDs leaking into model-visible bytes.
    print()
    print("F. INTERNAL-ID LEAK CHECK (latest round r8)")
    leaked = 0
    for tj in (OP / "engine_r8_flat").glob("*_trajectory.json"):
        d = json.loads(tj.read_text(encoding="utf-8"))
        for m in d.get("messages", []):
            if m.get("role") != "tool":
                continue
            c = str(m.get("content") or "")
            if re.search(r"obl-[0-9a-f]{6,}|pred-[0-9a-f]{6,}", c):
                leaked += 1
    print(f"model-visible internal-ID bytes in r8: {leaked} (0 = fixed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
