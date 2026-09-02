"""Provider-free audit of nested Mini-SWE/Harbor diagnostic artifacts."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from gt_engine.run_diagnostics import diagnose_artifact_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--write-summary", action="store_true")
    args = parser.parse_args(argv)
    report = diagnose_artifact_root(args.root, strict=args.strict)
    payload = report.to_mapping()
    rendered = json.dumps(payload, indent=2, sort_keys=True)
    print(rendered)
    if args.write_summary:
        (args.root / "diagnostic-summary.json").write_text(rendered + "\n", encoding="utf-8")
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        lines = [
            "## GroundTruth diagnostic summary", "",
            "| Task | Primary | Fingerprint |", "|---|---|---|",
        ]
        lines.extend(
            f"| {row['task_id']} | {row['primary_diagnostic']} | `{row['fingerprint'][:16]}` |"
            for row in payload["tasks"]
        )
        Path(summary_path).open("a", encoding="utf-8").write("\n".join(lines) + "\n")
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
