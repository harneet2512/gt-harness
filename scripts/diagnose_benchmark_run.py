"""Provider-free audit of nested Mini-SWE/Harbor diagnostic artifacts."""

from __future__ import annotations

import argparse
import json
import os
import sys
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
    # Summary retention is mandatory. The compatibility flag is accepted but
    # no longer controls whether the artifact exists.
    _ = args.write_summary
    (args.root / "diagnostic-summary.json").write_text(
        rendered + "\n", encoding="utf-8"
    )
    for event in report.diagnostics:
        line = (
            f"[GT][{event.severity}][{event.code.value}] task={event.task_id} "
            f"phase={event.phase} cause={event.normalized_cause}"
        )
        print(line, file=sys.stderr)
        if os.environ.get("GITHUB_ACTIONS") == "true":
            annotation = "error" if event.severity == "ERROR" else "warning"
            print(f"::{annotation} title={event.code.value}::{line}", file=sys.stderr)
    # Capabilities that did not work must be visible at the END of the task,
    # not only as an error string inside a receipt someone has to go and read.
    # A run whose language servers or embedder never came up finishes looking
    # normal otherwise, and the person reading the result is the last one who
    # should have to reconstruct that GT ran with less than GT has.
    def _worked(row: dict) -> str:
        """Tri-valued: a capability never asked to run did not fail to run.

        This column asks "did this work", and bold NO is the loudest cell in
        the table. For a capability deliberately switched off the answer is
        not no, it is not-applicable - printing NO moved the cry-wolf out of
        stderr and left it in the widget a human actually reads.
        """
        if row.get("verified"):
            return "yes"
        return "n/a" if not row.get("triggered") else "**NO**"

    capabilities = [row for row in payload.get("capabilities", [])
                    if isinstance(row, dict)]
    # Keyed on refused/degraded, not on `not verified`. UNEXERCISED also has
    # verified False, and it is what a capability deliberately switched off
    # reports - so a GT-off control run was named in the did-not-work line and
    # raised a CI error for doing exactly what it was asked. The table below
    # still shows it as not-worked, which is honest; being called out as a
    # failure is not.
    degraded = [row for row in capabilities
                if row.get("refused") or row.get("degraded")]
    for row in degraded:
        line = (
            f"[GT][CAPABILITY][{row.get('state')}] {row.get('capability')} "
            f"required={row.get('required')} evidence={row.get('evidence')}"
        )
        print(line, file=sys.stderr)
        if os.environ.get("GITHUB_ACTIONS") == "true":
            severity = "error" if row.get("required") else "warning"
            print(f"::{severity} title=capability_not_working::{line}", file=sys.stderr)

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
        if capabilities:
            lines.extend([
                "", "### Capabilities", "",
                "| Capability | State | Required | Worked | Evidence |",
                "|---|---|---|---|---|",
            ])
            lines.extend(
                f"| {row.get('capability')} | {row.get('state')} | "
                f"{'yes' if row.get('required') else 'no'} | "
                f"{_worked(row)} | "
                f"{row.get('evidence')} |"
                for row in capabilities
            )
            if degraded:
                names = ", ".join(str(row.get("capability")) for row in degraded)
                lines.extend(["", f"**These did not work: {names}**"])
        with Path(summary_path).open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
