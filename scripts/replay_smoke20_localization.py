#!/usr/bin/env python3
"""Provider-free localization truth run over the frozen DeepSWE smoke20 cohort.

For every cohort task this replays GroundTruth context compilation against the
exact task repository revision and audits the delivered roles against the
official reference-solution patch.  No model or provider is invoked.  The
reference patch is an audit oracle only; it is never retrieval input.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

from gt_harness.treatments import GroundTruthTreatment


def _compiler_fingerprint() -> str:
    root = Path(__file__).resolve().parents[1]
    hasher = hashlib.sha256()
    for relative in (
        "gt_engine/repository_context_compiler.py",
        "gt_harness/treatments.py",
    ):
        hasher.update(relative.encode("utf-8"))
        hasher.update(
            b"\0" + (root / relative).read_bytes() + b"\0"
        )
    return hasher.hexdigest()

_PATCH_PATH = re.compile(r"^\+\+\+ b/(.+?)\s*$", re.MULTILINE)
_ROLE_PREFIXES = (
    "EXACT_EDIT_TARGET",
    "INSPECT_CANDIDATE_NOT_EDIT_AUTHORITY",
    "INSPECT_PUBLIC_SURFACE",
    "INSPECT_INTEGRATION",
    "AFFECTED_TEST",
    "PROPOSED_NEW_FILE",
    "UNCOVERED_FACET",
    "BOUNDED_PROCESS",
    "BOUNDED_IMPACT",
)


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args], text=True, encoding="utf-8"
    ).strip()


def _patch_paths(text: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            path
            for path in _PATCH_PATH.findall(text)
            if not path.startswith("dev/null")
        )
    )


def _delivered_roles(context: str) -> dict[str, list[dict[str, str]]]:
    delivered: dict[str, list[dict[str, str]]] = {}
    for line in context.splitlines():
        stripped = line.strip()
        for prefix in _ROLE_PREFIXES:
            if not stripped.startswith(prefix + " "):
                continue
            rest = stripped[len(prefix) + 1 :]
            location = rest.split(" ", 1)[0]
            path = location.split(":", 1)[0]
            symbol = ""
            if "#" in location:
                symbol = location.split("#", 1)[1]
            delivered.setdefault(prefix, []).append({"path": path, "symbol": symbol})
            break
    return delivered


def run_case(task_id: str, row: dict[str, object], state_root: Path) -> dict[str, object]:
    repository = Path(str(row["repository"]))
    expected_sha = str(row["base_sha"])
    head = _git(repository, "rev-parse", "HEAD")
    if head != expected_sha:
        raise RuntimeError(
            f"{task_id}: repository revision mismatch: expected {expected_sha}, got {head}"
        )

    task_text = Path(str(row["instruction"])).read_text(encoding="utf-8")
    oracle_paths = set(_patch_paths(Path(str(row["solution"])).read_text(encoding="utf-8")))

    treatment = GroundTruthTreatment(
        repository,
        state_dir=state_root / task_id / "state",
        retrieval_mode="sparse_only",
    )
    context = treatment.prepare(task_text)
    receipt = treatment.finalize(None)

    delivered = _delivered_roles(context)
    edit_targets = delivered.get("EXACT_EDIT_TARGET", [])

    target_hits = [t for t in edit_targets if t["path"] in oracle_paths]
    wrong_targets = [t for t in edit_targets if t["path"] not in oracle_paths]
    precision = (
        len(target_hits) / len(edit_targets) if edit_targets else None
    )
    recall = len(target_hits) / len(oracle_paths) if oracle_paths else None

    return {
        "task_id": task_id,
        "commit": head,
        "treatment_status": receipt.get("treatment_status"),
        "graph_available": receipt.get("graph_available"),
        "context_schema_v6": 'schema="gt.agent_context.v6"' in context,
        "edit_target_count": len(edit_targets),
        "edit_targets": edit_targets,
        "wrong_edit_targets": wrong_targets,
        "oracle_path_count": len(oracle_paths),
        "edit_target_precision": precision,
        "edit_target_recall": recall,
        "roles": {
            name: len(entries) for name, entries in delivered.items()
        },
        "role_paths": {
            name: sorted({entry["path"] for entry in entries})
            for name, entries in delivered.items()
        },
        "oracle_paths": sorted(oracle_paths),
        "token_count": receipt.get("initial_context_token_count"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--tasks", type=Path, default=None)
    parser.add_argument("--out-json", type=Path, required=True)
    args = parser.parse_args()

    manifest_rows = json.loads(args.manifest.read_text(encoding="utf-8"))
    only_tasks = None
    if args.tasks is not None:
        only_tasks = frozenset(
            line.strip()
            for line in args.tasks.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )

    results = []
    failures: list[str] = []
    for row in manifest_rows:
        task_id = str(row["task_id"])
        if only_tasks is not None and task_id not in only_tasks:
            continue
        case_dir = args.state_root / task_id
        case_dir.mkdir(parents=True, exist_ok=True)
        try:
            results.append(run_case(task_id, row, args.state_root))
            print(f"{task_id}: OK", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - gate must record per-task failure
            failures.append(f"{task_id}: {exc}")
            print(f"{task_id}: FAIL {exc}", file=sys.stderr)

    audited = [
        r for r in results if r["edit_target_precision"] is not None
    ]
    wrong_symbol_tasks = [
        r["task_id"] for r in results if r["wrong_edit_targets"]
    ]
    summary = {
        "schema": "gt.localization_truth_report.v1",
        "compiler_fingerprint": _compiler_fingerprint(),
        "cases_run": len(results),
        "case_failures": failures,
        "mean_edit_target_precision": (
            round(
                sum(r["edit_target_precision"] for r in audited) / len(audited), 4
            )
            if audited
            else None
        ),
        "tasks_with_wrong_edit_targets": wrong_symbol_tasks,
        "zero_target_tasks": [
            r["task_id"] for r in results if r["edit_target_count"] == 0
        ],
        "treatment_failures": [
            {"task": r["task_id"], "status": r["treatment_status"]}
            for r in results
            if r["treatment_status"] not in ("ACTIVE", "NOT_APPLICABLE")
        ],
    }
    args.out_json.write_text(
        json.dumps({"summary": summary, "results": results}, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
