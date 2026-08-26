#!/usr/bin/env python3
"""Build the typed flip ledger for the frozen DeepSWE smoke20 cohort.

Joins one GT-on artifact tree against one GT-off baseline tree using official
verifier results only.  Comparator patch paths are applied strictly post hoc
as a localization audit oracle; they are never runtime retrieval input.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

_PATCH_PATH = re.compile(r"^\+\+\+ b/(.+?)\s*$", re.MULTILINE)
_TARGET_LINE = re.compile(r"^EXACT_EDIT_TARGET (\S+)")

QUADRANTS = ("both_solved", "gt_only", "baseline_only", "both_failed")


def _reward_official(result: dict[str, object]) -> tuple[float | None, str]:
    status = str(result.get("status") or "")
    reward = result.get("reward")
    if status.upper() == "GRADED" and isinstance(reward, (int, float)):
        return float(reward), "official_verifier"
    return None, f"unofficial:{status or 'missing'}"


def _find_one(root: Path, name: str) -> Path | None:
    matches = sorted(root.rglob(name))
    return matches[0] if matches else None


def _patch_paths(patch_file: Path | None) -> tuple[str, ...]:
    if patch_file is None:
        return ()
    text = patch_file.read_text(encoding="utf-8", errors="replace")
    paths = tuple(dict.fromkeys(_PATCH_PATH.findall(text)))
    return tuple(p for p in paths if not p.startswith("dev/null"))


def _edit_targets(context: str) -> tuple[dict[str, str], ...]:
    targets: list[dict[str, str]] = []
    for line in context.splitlines():
        match = _TARGET_LINE.match(line.strip())
        if match is None:
            continue
        location = match.group(1)
        path, _, symbol = location.partition("#")
        req = ""
        req_match = re.search(r"req=([^\s]+)", line)
        if req_match is not None:
            req = req_match.group(1)
        targets.append(
            {
                "location": location,
                "path": path.split(":", 1)[0],
                "symbol": symbol,
                "requirement": req,
            }
        )
    return tuple(targets)


def _load_task_dir(root: Path, task_id: str) -> Path | None:
    candidates = [
        path
        for path in root.glob("*/*")
        if path.is_dir() and task_id in path.name
    ]
    if not candidates:
        candidates = [
            child for child in root.iterdir() if child.is_dir() and task_id in child.name
        ]
    return candidates[0] if len(candidates) == 1 else None


@dataclass
class TaskRow:
    task_id: str
    gt_reward: float | None = None
    gt_reward_source: str = "missing"
    baseline_reward: float | None = None
    baseline_reward_source: str = "missing"
    treatment_status: str = "unknown"
    provider_calls: int | None = None
    delivery_count: int | None = None
    edit_targets: tuple[dict[str, str], ...] = field(default_factory=tuple)
    gt_patch_paths: tuple[str, ...] = ()
    oracle_patch_paths: tuple[str, ...] = ()

    @property
    def quadrant(self) -> str:
        g, b = self.gt_reward, self.baseline_reward
        if g is None or b is None:
            return "ungraded"
        if g >= 1.0 and b >= 1.0:
            return "both_solved"
        if g >= 1.0:
            return "gt_only"
        if b >= 1.0:
            return "baseline_only"
        return "both_failed"

    def precision(self) -> float | None:
        if not self.edit_targets or not self.oracle_patch_paths:
            return None
        oracle = set(self.oracle_patch_paths)
        hits = sum(1 for t in self.edit_targets if t["path"] in oracle)
        return hits / len(self.edit_targets)

    def oracle_recall(self) -> float | None:
        if not self.oracle_patch_paths:
            return None
        if not self.edit_targets:
            return 0.0
        oracle = set(self.oracle_patch_paths)
        hits = sum(1 for t in self.edit_targets if t["path"] in oracle)
        return hits / len(oracle)


def collect(gt_root: Path, baseline_root: Path, cohort: dict) -> list[TaskRow]:
    rows: list[TaskRow] = []
    baseline_rewards = {
        str(task): value
        for task, value in (cohort.get("baseline", {}).get("task_rewards") or {}).items()
    }
    for task_id in cohort["task_ids"]:
        row = TaskRow(task_id=task_id)

        gt_dir = _load_task_dir(gt_root, task_id) if gt_root else None
        if gt_dir is not None:
            verifier = _find_one(gt_dir, "official-verifier-result.json")
            if verifier is not None:
                result = json.loads(verifier.read_text(encoding="utf-8"))
                row.gt_reward, row.gt_reward_source = _reward_official(result)
                if isinstance(row.baseline_reward, type(None)) and task_id in baseline_rewards:
                    pass
            run_file = _find_one(gt_dir, "gt-run.json")
            if run_file is not None:
                run = json.loads(run_file.read_text(encoding="utf-8"))
                receipt = run.get("treatment_receipt") or {}
                row.treatment_status = str(receipt.get("treatment_status") or "unknown")
                row.provider_calls = run.get("provider_calls")
                row.delivery_count = receipt.get("delivery_count")
                context = str(receipt.get("initial_context") or "")
                row.edit_targets = _edit_targets(context)
            patch = _find_one(gt_dir, "model.patch")
            row.gt_patch_paths = _patch_paths(patch)

        if task_id in baseline_rewards:
            row.baseline_reward = float(baseline_rewards[task_id])
            row.baseline_reward_source = "cohort_frozen"

        base_dir = _load_task_dir(baseline_root, task_id) if baseline_root else None
        if base_dir is not None:
            reward_file = _find_one(base_dir, "reward.json")
            if reward_file is not None and reward_file.parent.name == "verifier":
                data = json.loads(reward_file.read_text(encoding="utf-8"))
                if isinstance(data.get("reward"), (int, float)):
                    frozen = row.baseline_reward
                    row.baseline_reward = float(data["reward"])
                    row.baseline_reward_source = (
                        "verifier_reward_json"
                        if frozen is None or frozen == row.baseline_reward
                        else f"conflict:frozen={frozen}"
                    )
            row.oracle_patch_paths = _patch_paths(_find_one(base_dir, "model.patch"))

        rows.append(row)
    return rows


def summarize(rows: list[TaskRow]) -> dict[str, object]:
    quadrants = {name: sorted(r.task_id for r in rows if r.quadrant == name) for name in QUADRANTS}
    ungraded = [r.task_id for r in rows if r.quadrant == "ungraded"]
    audited = [r for r in rows if r.precision() is not None]
    regressed = [r for r in rows if r.quadrant == "baseline_only"]
    solved_by_gt = [r for r in rows if r.gt_reward is not None and r.gt_reward >= 1.0]

    def mean(values: list[float]) -> float | None:
        return round(sum(values) / len(values), 4) if values else None

    return {
        "schema": "gt.deepswe_smoke_flip_ledger.v1",
        "totals": {
            "tasks": len(rows),
            "gt_solved": len(solved_by_gt),
            "baseline_solved": sum(
                1 for r in rows if r.baseline_reward is not None and r.baseline_reward >= 1.0
            ),
            "quadrant_counts": {name: len(ids) for name, ids in quadrants.items()},
            "ungraded": ungraded,
        },
        "quadrants": quadrants,
        "localization_audit": {
            "tasks_with_edit_targets": len(audited),
            "mean_edit_target_precision": mean(
                [r.precision() for r in audited if r.precision() is not None]
            ),
            "regressed_task_precision": {
                r.task_id: r.precision()
                for r in regressed
                if r.precision() is not None
            },
            "solved_task_precision": {
                r.task_id: r.precision()
                for r in solved_by_gt
                if r.precision() is not None
            },
        },
        "treatment_failures": [
            {"task": r.task_id, "status": r.treatment_status}
            for r in rows
            if r.treatment_status != "ACTIVE"
        ],
    }


def render_markdown(summary: dict, rows: list[TaskRow]) -> str:
    lines = [
        "# DeepSWE smoke20 flip ledger",
        "",
        f"GT solved {summary['totals']['gt_solved']}/{summary['totals']['tasks']} "
        f"vs baseline {summary['totals']['baseline_solved']}.",
        "",
        "| Quadrant | Count | Tasks |",
        "|---|---:|---|",
    ]
    for name, ids in summary["quadrants"].items():
        lines.append(f"| {name} | {len(ids)} | {', '.join(ids) or '—'} |")

    lines += ["", "## Per-task localization audit", "",
              "| Task | Quadrant | Treatment | Edit targets | Precision vs oracle |",
              "|---|---|---|---|---:|"]
    for row in sorted(rows, key=lambda r: (r.quadrant, r.task_id)):
        targets = ", ".join(t["location"] for t in row.edit_targets[:3]) or "—"
        precision = row.precision()
        precision_text = "n/a" if precision is None else f"{precision:.2f}"
        lines.append(
            f"| {row.task_id} | {row.quadrant} | {row.treatment_status} "
            f"| {targets} | {precision_text} |"
        )
    failures = summary["treatment_failures"]
    if failures:
        lines += ["", "## Treatment failures", ""]
        lines += [f"- `{f['task']}`: {f['status']}" for f in failures]
    audit = summary["localization_audit"]
    lines += [
        "",
        f"Mean edit-target precision across audited tasks: "
        f"{audit['mean_edit_target_precision']}",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gt-root", type=Path, required=True)
    parser.add_argument("--baseline-root", type=Path, required=True)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--out-json", type=Path, required=True)
    parser.add_argument("--out-md", type=Path, required=True)
    args = parser.parse_args()

    cohort = json.loads(args.cohort.read_text(encoding="utf-8"))
    rows = collect(args.gt_root, args.baseline_root, cohort)
    summary = summarize(rows)
    args.out_json.write_text(
        json.dumps(
            {
                **summary,
                "tasks": [
                    {
                        "task_id": r.task_id,
                        "quadrant": r.quadrant,
                        "gt_reward": r.gt_reward,
                        "baseline_reward": r.baseline_reward,
                        "treatment_status": r.treatment_status,
                        "provider_calls": r.provider_calls,
                        "delivery_count": r.delivery_count,
                        "edit_targets": list(r.edit_targets),
                        "gt_patch_paths": list(r.gt_patch_paths),
                        "oracle_patch_paths": list(r.oracle_patch_paths),
                    }
                    for r in rows
                ],
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    args.out_md.write_text(render_markdown(summary, rows), encoding="utf-8")
    print(json.dumps(summary["totals"], indent=2))
    print(json.dumps(summary["localization_audit"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
