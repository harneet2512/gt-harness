"""Audit replay bundles for exact first-intervention decision-point pairs."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from gt_engine.decision_point_eval import validate_decision_point_row
from gt_engine.replay_bundle import load_replay_bundle


def _bundle_paths(roots: list[Path]) -> list[Path]:
    found: set[Path] = set()
    for root in roots:
        if root.name == "gt_replay" and (root / "manifest.json").is_file():
            found.add(root.resolve())
            continue
        if root.is_dir():
            found.update(
                path.parent.resolve()
                for path in root.rglob("manifest.json")
                if path.parent.name == "gt_replay"
            )
    return sorted(found)


def audit_bundles(roots: list[Path]) -> dict:
    bundles = _bundle_paths(roots)
    validity: Counter[str] = Counter()
    valid_cases: list[dict] = []
    corrupt: list[dict] = []
    calls = 0
    for bundle in bundles:
        try:
            loaded = load_replay_bundle(bundle)
        except ValueError as exc:
            corrupt.append({"bundle": str(bundle), "error": str(exc)})
            continue
        task_id = bundle.parent.parent.name.split("__", 1)[0]
        for row in loaded["calls"]:
            calls += 1
            result = validate_decision_point_row(row, task_id=task_id)
            validity[result.validity.value] += 1
            if result.case is not None:
                valid_cases.append(
                    {
                        "task_id": result.case.task_id,
                        "call": result.case.call,
                        "model_name": result.case.model_name,
                        "source_revision": result.case.source_revision,
                        "workspace_revision": result.case.workspace_revision,
                        "payload_chars": len(result.case.payload),
                        "selected_contribution_ids": list(
                            result.case.selected_contribution_ids
                        ),
                    }
                )
    return {
        "schema": "gt.decision_point_capture_audit.v1",
        "bundle_count": len(bundles),
        "call_count": calls,
        "valid_case_count": len(valid_cases),
        "validity_counts": dict(sorted(validity.items())),
        "valid_cases": valid_cases,
        "corrupt_bundles": corrupt,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("roots", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--min-valid", type=int, default=0)
    args = parser.parse_args()
    report = audit_bundles(args.roots)
    body = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(body + "\n", encoding="utf-8")
    print(body)
    return 0 if report["valid_case_count"] >= max(0, args.min_valid) else 2


if __name__ == "__main__":
    raise SystemExit(main())
