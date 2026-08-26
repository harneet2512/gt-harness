#!/usr/bin/env python3
"""Fail-closed audit of the authoritative GT release documentation."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

DEFAULT_DOCUMENTS = (
    Path("arch_type.md"),
    Path("CANONICAL_ARCHITECTURE.md"),
    Path("docs/GT_MECHANICAL_COMPLETENESS_CONTRACT.md"),
    Path("docs/GT_RELEASE_DOSSIER.md"),
    Path("docs/GT_COMPLETE_IMPLEMENTATION_RECORD_2026-08-21.md"),
    Path("docs/GT_FINAL_HANDOFF_REMAINING_WORK_2026-08-21.md"),
    Path("docs/GT_BENCHMARK_READINESS_AUTHORITY_2026-08-21.md"),
    Path("docs/gt_gitnexus_program/01_GT_CURRENT_ARCHITECTURE.md"),
    Path("docs/gt_gitnexus_program/02_GT_20_TASK_CAUSAL_RECEIPTS.md"),
    Path("docs/gt_gitnexus_program/03_GT_FEATURE_LEDGER.md"),
    Path("docs/gt_gitnexus_program/04_GT_NEGATIVE_FLIP_AUTOPSY.md"),
    Path("docs/gt_gitnexus_program/05_GT_POSITIVE_FLIP_AUTOPSY.md"),
    Path("docs/gt_gitnexus_program/06_GT_UNUSED_ASSETS.md"),
    Path("docs/gt_gitnexus_program/07_GITNEXUS_ARCHITECTURE.md"),
    Path("docs/gt_gitnexus_program/08_GITNEXUS_RESOLUTION_AND_UNCERTAINTY.md"),
    Path("docs/gt_gitnexus_program/09_GITNEXUS_DELIVERY_AND_LIFECYCLE.md"),
    Path("docs/gt_gitnexus_program/10_GITNEXUS_BENCHMARK_FORENSICS.md"),
    Path("docs/gt_gitnexus_program/11_GT_VS_GITNEXUS_CAPABILITY_MATRIX.md"),
    Path("docs/gt_gitnexus_program/12_FAILURE_TO_MECHANISM_MATRIX.md"),
    Path("docs/gt_gitnexus_program/13_OUTPERFORMANCE_OPPORTUNITIES.md"),
    Path("docs/gt_gitnexus_program/14_IMPLEMENTATION_PLAN.md"),
    Path("docs/gt_gitnexus_program/15_20_TASK_RERUN_REPORT.md"),
    Path("docs/gt_gitnexus_program/16_NEXT_ABLATION_PLAN.md"),
    Path("docs/gt_gitnexus_program/17_DIRECT_GITNEXUS_COMPARISON_PLAN.md"),
    Path("docs/gt_gitnexus_program/20_FINAL_REGRESSION_CONTROL_AND_BENCHMARK_READINESS.md"),
)
_REQUIRED_TERMS = {
    "arch_type.md": (
        "## Definition of `arch_type`",
        "## End-to-end production flow",
        "## Layer 3: structural graph substrate",
        "## Layer 6: provider-visible delivery",
        "## Failure policy",
        "## Verification map",
    ),
    "CANONICAL_ARCHITECTURE.md": (
        "[`arch_type.md`](arch_type.md)",
        "eval/benchmark_product_contract.json",
        "emitted exact-SHA receipts",
    ),
    "GT_MECHANICAL_COMPLETENESS_CONTRACT.md": (
        "gt.task_execution_certificate.v1",
        "PROVEN_NOT_APPLICABLE",
        "MechanicalCompletenessBlocked",
        "hidden_reasoning_inferred",
        "repair20-v1",
        "gt.provider_value.v1",
    ),
    "GT_RELEASE_DOSSIER.md": (
        "active_release.json",
        "GT_MECHANICAL_COMPLETENESS=PASS",
        "central_relational_v2",
        "central_provider_free.yml",
        "mechanical_completeness: PASS",
        "does not embed a mutable latest-run ID",
        "scripts.harbor_results",
    ),
}
_FORBIDDEN_OUTCOME_CLAIMS = (
    "100% solve guaranteed",
    "all tasks will solve",
    "18/18 fired",
)
_LINK = re.compile(r"\[[^]]+\]\(([^)]+)\)")


def audit_documentation(
    root: Path,
    *,
    documents: tuple[Path, ...] | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    paths = tuple(
        path if path.is_absolute() else root / path
        for path in (documents or DEFAULT_DOCUMENTS)
    )
    failures: list[str] = []
    for path in paths:
        if not path.is_file():
            failures.append(f"missing_document:{path.name}")
            continue
        text = path.read_text(encoding="utf-8")
        for phrase in _FORBIDDEN_OUTCOME_CLAIMS:
            if phrase.lower() in text.lower():
                failures.append(f"unearned_outcome_claim:{path.name}:{phrase}")
        for term in _REQUIRED_TERMS.get(path.name, ()):
            if term not in text:
                failures.append(f"missing_required_term:{path.name}:{term}")
        for target in _LINK.findall(text):
            if target.startswith(("http://", "https://", "#")):
                continue
            relative = target.split("#", 1)[0]
            if relative and not (path.parent / relative).resolve().exists():
                failures.append(f"broken_link:{path.name}:{target}")
    return {
        "schema": "gt.documentation_consistency.v1",
        "status": "PASS" if not failures else "BLOCKED",
        "checked_documents": len(paths),
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = audit_documentation(args.root)
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output is not None:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
