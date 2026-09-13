#!/usr/bin/env python3
"""Suite descriptors binding a paid benchmark cohort to its attestation contract.

The DeepSWE smoke20 and the SWE-bench-Live Lite smoke share one attestation
shape: a plan receipt minted before dispatch, per-task runner/product/verifier
evidence, and a suite-scoped final attestation. What differs between them is
exactly the data a suite descriptor pins down here: where the canonical task
inventory lives, which task is the gate canary, what the plan and attestation
schemas are named, and how the benchmark's immutable revision is expressed
(a git sha for the externally-hosted DeepSWE snapshot, a manifest digest for
the in-repo SWE-bench-Live cohort).

Stage semantics are identical across suites and implemented once: gate-one runs
the canary, the remainder stage runs everything else bound to that canary's
passed attestation, the all-stage runs the whole cohort, and single:/subset:
name tasks explicitly. A descriptor only supplies the nouns.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scripts.smoke_stage import GATE_STAGE, named_task_ids

ROOT = Path(__file__).resolve().parents[1]


def _object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    return value


@dataclass(frozen=True)
class BenchmarkSuite:
    """The immutable nouns one paid benchmark cohort attests against."""

    suite_id: str
    gate_task_id: str
    remainder_stage: str
    all_stage: str
    gate_max_timeout_seconds: float
    canonical_task_ids: tuple[str, ...]
    plan_filename: str
    plan_schema: str
    attestation_filename: str
    attestation_schema: str
    attestation_error_schema: str
    benchmark_sha: str
    task_config_identity: str
    trusted_tasks: dict[str, dict[str, Any]] = field(compare=False)
    baseline_required: bool = True

    @property
    def stages(self) -> frozenset[str]:
        return frozenset({GATE_STAGE, self.remainder_stage, self.all_stage})


def _deepswe_suite() -> BenchmarkSuite:
    manifest = _object(ROOT / "eval" / "deepswe_smoke20_v1.json")
    bundle = _object(ROOT / "config" / "deepswe_product_bundle_v1.json")
    task_ids = tuple(str(task) for task in manifest.get("task_ids") or [])
    benchmark_sha = str(manifest.get("benchmark_sha") or "")
    dataset_commit = str((bundle.get("dataset") or {}).get("commit") or "")
    if benchmark_sha != dataset_commit:
        # The product bundle and the cohort manifest disagree about which
        # benchmark revision is pinned; there is no honest suite to attest.
        raise ValueError("deepswe manifest and bundle pin different benchmark shas")
    return BenchmarkSuite(
        suite_id="deepswe",
        gate_task_id="aiomonitor-task-snapshots-diff",
        remainder_stage="remaining-19",
        all_stage="all-20",
        gate_max_timeout_seconds=90 * 60,
        canonical_task_ids=task_ids,
        plan_filename="deepswe20-plan.json",
        plan_schema="gt.deepswe_gt_harness_plan.v1",
        attestation_filename="deepswe20-attestation.json",
        attestation_schema="gt.deepswe_gt_harness_attestation.v1",
        attestation_error_schema="gt.deepswe_gt_harness_attestation_error.v1",
        benchmark_sha=benchmark_sha,
        task_config_identity=str(
            (bundle.get("dataset") or {}).get("task_config_identity") or ""
        ),
        trusted_tasks={
            str(row.get("task_id")): row
            for row in bundle.get("tasks", [])
            if isinstance(row, dict)
        },
        baseline_required=True,
    )


def _swelive_suite() -> BenchmarkSuite:
    manifest_path = ROOT / "swelive-bench" / "manifest.json"
    manifest = _object(manifest_path)
    if manifest.get("schema") != "swelive-bench.manifest/v1":
        raise ValueError("invalid SWE-bench-Live manifest schema")
    tasks = manifest.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("SWE-bench-Live manifest carries no tasks")
    rows = [row for row in tasks if isinstance(row, dict)]
    task_ids = tuple(str(row.get("task_id")) for row in rows)
    if len(task_ids) != len(set(task_ids)) or len(task_ids) != len(rows):
        raise ValueError("SWE-bench-Live manifest task set is not unique")
    return BenchmarkSuite(
        suite_id="swelive",
        # The gate canary is the manifest's ordinal-1 task: a small Python
        # repository whose evaluation image is already pinned by digest.
        gate_task_id=task_ids[0],
        remainder_stage="remaining",
        all_stage="all",
        # Same rail as the DeepSWE gate: the canary runs at the cohort's own
        # declared timeout (1800 s for both Lite tasks) so a gate result is
        # comparable to the cohort it gates.
        gate_max_timeout_seconds=1800.0,
        canonical_task_ids=task_ids,
        plan_filename="swelive-plan.json",
        plan_schema="gt.swelive_gt_harness_plan.v1",
        attestation_filename="swelive-attestation.json",
        attestation_schema="gt.swelive_gt_harness_attestation.v1",
        attestation_error_schema="gt.swelive_gt_harness_attestation_error.v1",
        # The in-repo cohort has no external revision; its immutable identity
        # is the manifest's own bytes at the attested source sha.
        benchmark_sha=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        task_config_identity=str(manifest.get("task_config_identity") or ""),
        trusted_tasks={str(row.get("task_id")): row for row in rows},
        # No GT-off baseline exists for SWE-bench-Live and none may be run;
        # the plan carries a dataset reference instead of a comparator.
        baseline_required=False,
    )


def load_suite(suite: str) -> BenchmarkSuite:
    if suite == "deepswe":
        return _deepswe_suite()
    if suite == "swelive":
        return _swelive_suite()
    raise ValueError(f"unsupported benchmark suite: {suite}")


def select_stage_tasks(
    suite: BenchmarkSuite, tasks: list[str] | tuple[str, ...], stage: str
) -> list[str]:
    ordered = list(tasks)
    canonical = list(suite.canonical_task_ids)
    if ordered != canonical or len(set(ordered)) != len(canonical):
        raise ValueError(f"invalid canonical {suite.suite_id} task inventory")
    if stage == GATE_STAGE:
        return [suite.gate_task_id]
    if stage == suite.remainder_stage:
        return [task for task in ordered if task != suite.gate_task_id]
    if stage == suite.all_stage:
        return ordered
    named = named_task_ids(stage)
    if named:
        unknown = [task for task in named if task not in ordered]
        if unknown:
            raise ValueError(
                "named stage includes a task outside the canonical cohort"
            )
        return [task for task in ordered if task in set(named)]
    raise ValueError(
        f"cohort_stage must be {GATE_STAGE}, {suite.remainder_stage}, "
        f"{suite.all_stage} or single:<task>"
    )


def validate_stage_inputs(
    suite: BenchmarkSuite, stage: str, prior_gate_run_id: str
) -> None:
    run_id = prior_gate_run_id.strip()
    named = named_task_ids(stage)
    if (stage in (GATE_STAGE, suite.all_stage) or named) and run_id:
        raise ValueError(f"{stage} must not claim a prior gate run")
    if stage == suite.remainder_stage and not re.fullmatch(r"[1-9][0-9]*", run_id):
        raise ValueError(
            f"{suite.remainder_stage} requires a positive prior_gate_run_id"
        )
    if stage not in suite.stages and not named:
        raise ValueError("unknown cohort stage")


def stage_timeout_cap_seconds(suite: BenchmarkSuite, stage: str) -> float | None:
    if stage == GATE_STAGE or named_task_ids(stage):
        return float(suite.gate_max_timeout_seconds)
    if stage in (suite.remainder_stage, suite.all_stage):
        return None
    raise ValueError(
        f"cohort_stage must be {GATE_STAGE}, {suite.remainder_stage} "
        f"or {suite.all_stage}"
    )


def validate_prior_gate(
    suite: BenchmarkSuite,
    root: Path,
    *,
    source_sha: str,
    prior_gate_run_id: str,
) -> dict[str, object]:
    validate_stage_inputs(suite, suite.remainder_stage, prior_gate_run_id)
    attestation_path = root / suite.attestation_filename
    diagnostics_path = root / "diagnostic-summary.json"
    attestation = _object(attestation_path)
    diagnostics = _object(diagnostics_path)
    if (
        not re.fullmatch(r"[0-9a-f]{40}", source_sha)
        or attestation.get("schema") != suite.attestation_schema
        or attestation.get("status") != "PASS"
        or attestation.get("source_sha") != source_sha
        or str(attestation.get("workflow_run_id")) != prior_gate_run_id
        or attestation.get("task_job_result") != "success"
        or attestation.get("task_count") != 1
        or attestation.get("task_ids") != [suite.gate_task_id]
        or attestation.get("official_verifier_tasks") != [suite.gate_task_id]
    ):
        raise ValueError(
            "prior gate attestation is not a complete exact-source gate-one run"
        )
    totals = attestation.get("product_totals")
    if (
        not isinstance(totals, dict)
        or type(totals.get("provider_calls")) is not int
        or totals.get("provider_calls", 0) < 1
        or type(totals.get("provider_completed_calls")) is not int
        or totals.get("provider_completed_calls", 0) < 1
    ):
        raise ValueError("prior gate lacks completed provider-call evidence")
    capability_rows = diagnostics.get("capabilities")
    if (
        diagnostics.get("schema") != "gt.diagnostic_summary.v1"
        or diagnostics.get("exit_code") != 0
        or diagnostics.get("artifact_issues") != []
        or [row.get("task_id") for row in diagnostics.get("tasks", [])]
        != [suite.gate_task_id]
        or not isinstance(capability_rows, list)
        or not capability_rows
    ):
        raise ValueError("prior gate diagnostics are not healthy and complete")
    for row in capability_rows:
        if not isinstance(row, dict) or row.get("task_id") != suite.gate_task_id:
            raise ValueError("prior gate capability identity mismatch")
        if row.get("required") and (
            row.get("state") != "WORKING" or row.get("verified") is not True
        ):
            raise ValueError(
                "prior gate required capability is not independently verified"
            )
    return {
        "schema": "gt.prior_gate_binding.v1",
        "workflow_run_id": int(prior_gate_run_id),
        "task_id": suite.gate_task_id,
        "source_sha": source_sha,
        "attestation_sha256": hashlib.sha256(
            attestation_path.read_bytes()
        ).hexdigest(),
        "diagnostic_summary_sha256": hashlib.sha256(
            diagnostics_path.read_bytes()
        ).hexdigest(),
    }


__all__ = [
    "GATE_STAGE",
    "BenchmarkSuite",
    "load_suite",
    "named_task_ids",
    "select_stage_tasks",
    "stage_timeout_cap_seconds",
    "validate_prior_gate",
    "validate_stage_inputs",
]
