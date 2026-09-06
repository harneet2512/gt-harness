"""Fail-closed staging for the one-task then remaining-19 paid smoke."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

GATE_STAGE = "gate-one"
REMAINDER_STAGE = "remaining-19"
STAGES = frozenset({GATE_STAGE, REMAINDER_STAGE})
GATE_TASK_ID = "arktype-json-schema-refs-dependencies"
# Gate-one proves the infrastructure, and it can only do that if the task is
# allowed to finish. The 30-minute cap left the agent 1500s after the 300s
# supervisor grace, against a task whose own task.toml allows 5400s and a
# deepseek-v4-flash baseline whose MEAN task duration is 1439s - so the budget
# sat on the mean and run 34062325608 died at terminal=timeout with
# receipt_issuance=supervisor:deadline_exceeded, having built its graph and run
# the model loop correctly for the full 25 minutes. A proof that cannot reach a
# verdict is not a proof. The cap now matches the task's own allowance, which
# is the largest value that changes nothing else about the stage.
GATE_ONE_MAX_TIMEOUT_SECONDS = 90 * 60


def stage_timeout_cap_seconds(stage: str) -> float | None:
    if stage == GATE_STAGE:
        return float(GATE_ONE_MAX_TIMEOUT_SECONDS)
    if stage == REMAINDER_STAGE:
        return None
    raise ValueError("cohort_stage must be gate-one or remaining-19")


def select_stage_tasks(tasks: Sequence[str], stage: str) -> list[str]:
    ordered = list(tasks)
    if len(ordered) != 20 or len(set(ordered)) != 20 or GATE_TASK_ID not in ordered:
        raise ValueError("invalid canonical smoke20 task inventory")
    if stage == GATE_STAGE:
        return [GATE_TASK_ID]
    if stage == REMAINDER_STAGE:
        return [task for task in ordered if task != GATE_TASK_ID]
    raise ValueError("cohort_stage must be gate-one or remaining-19")


def validate_stage_inputs(stage: str, prior_gate_run_id: str) -> None:
    run_id = prior_gate_run_id.strip()
    if stage == GATE_STAGE and run_id:
        raise ValueError("gate-one must not claim a prior gate run")
    if stage == REMAINDER_STAGE and not re.fullmatch(r"[1-9][0-9]*", run_id):
        raise ValueError("remaining-19 requires a positive prior_gate_run_id")
    if stage not in STAGES:
        raise ValueError("unknown cohort stage")


def _object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    return value


def validate_prior_gate(
    root: Path, *, source_sha: str, prior_gate_run_id: str
) -> dict[str, object]:
    validate_stage_inputs(REMAINDER_STAGE, prior_gate_run_id)
    attestation_path = root / "deepswe20-attestation.json"
    diagnostics_path = root / "diagnostic-summary.json"
    attestation = _object(attestation_path)
    diagnostics = _object(diagnostics_path)
    if (
        not re.fullmatch(r"[0-9a-f]{40}", source_sha)
        or attestation.get("schema") != "gt.deepswe_gt_harness_attestation.v1"
        or attestation.get("status") != "PASS"
        or attestation.get("source_sha") != source_sha
        or str(attestation.get("workflow_run_id")) != prior_gate_run_id
        or attestation.get("task_job_result") != "success"
        or attestation.get("task_count") != 1
        or attestation.get("task_ids") != [GATE_TASK_ID]
        or attestation.get("official_verifier_tasks") != [GATE_TASK_ID]
    ):
        raise ValueError("prior gate attestation is not a complete exact-source gate-one run")
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
        != [GATE_TASK_ID]
        or not isinstance(capability_rows, list)
        or not capability_rows
    ):
        raise ValueError("prior gate diagnostics are not healthy and complete")
    for row in capability_rows:
        if not isinstance(row, dict) or row.get("task_id") != GATE_TASK_ID:
            raise ValueError("prior gate capability identity mismatch")
        if row.get("required") and (
            row.get("state") != "WORKING" or row.get("verified") is not True
        ):
            raise ValueError("prior gate required capability is not independently verified")
    return {
        "schema": "gt.prior_gate_binding.v1",
        "workflow_run_id": int(prior_gate_run_id),
        "task_id": GATE_TASK_ID,
        "source_sha": source_sha,
        "attestation_sha256": hashlib.sha256(attestation_path.read_bytes()).hexdigest(),
        "diagnostic_summary_sha256": hashlib.sha256(diagnostics_path.read_bytes()).hexdigest(),
    }


__all__ = [
    "GATE_ONE_MAX_TIMEOUT_SECONDS",
    "GATE_STAGE",
    "GATE_TASK_ID",
    "REMAINDER_STAGE",
    "select_stage_tasks",
    "stage_timeout_cap_seconds",
    "validate_prior_gate",
    "validate_stage_inputs",
]
