from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.smoke_stage import (
    GATE_TASK_ID,
    select_stage_tasks,
    validate_prior_gate,
    validate_stage_inputs,
)


def _tasks() -> list[str]:
    return [GATE_TASK_ID, *(f"task-{index}" for index in range(19))]


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def test_stage_selection_partitions_the_frozen_cohort_exactly_once() -> None:
    tasks = _tasks()
    gate = select_stage_tasks(tasks, "gate-one")
    remainder = select_stage_tasks(tasks, "remaining-19")

    assert gate == [GATE_TASK_ID]
    assert len(remainder) == 19
    assert gate + remainder == tasks
    assert set(gate).isdisjoint(remainder)


@pytest.mark.parametrize("stage", ["", "all", "smoke20", "gate_one"])
def test_unknown_stage_fails_closed(stage: str) -> None:
    with pytest.raises(ValueError):
        select_stage_tasks(_tasks(), stage)


def test_stage_inputs_require_an_exact_prior_run_only_for_remainder() -> None:
    validate_stage_inputs("gate-one", "")
    validate_stage_inputs("remaining-19", "123")
    with pytest.raises(ValueError):
        validate_stage_inputs("gate-one", "123")
    with pytest.raises(ValueError):
        validate_stage_inputs("remaining-19", "")


def _prior_gate(root: Path) -> None:
    _write(
        root / "deepswe20-attestation.json",
        {
            "schema": "gt.deepswe_gt_harness_attestation.v1",
            "status": "PASS",
            "source_sha": "a" * 40,
            "workflow_run_id": "123",
            "task_job_result": "success",
            "task_count": 1,
            "task_ids": [GATE_TASK_ID],
            "official_verifier_tasks": [GATE_TASK_ID],
            "product_totals": {"provider_calls": 2, "provider_completed_calls": 2},
        },
    )
    _write(
        root / "diagnostic-summary.json",
        {
            "schema": "gt.diagnostic_summary.v1",
            "exit_code": 0,
            "artifact_issues": [],
            "tasks": [{"task_id": GATE_TASK_ID}],
            "capabilities": [
                {
                    "task_id": GATE_TASK_ID,
                    "capability": "dense_retrieval",
                    "required": True,
                    "state": "WORKING",
                    "verified": True,
                }
            ],
        },
    )


def test_prior_gate_binding_requires_passed_attestation_and_working_capabilities(
    tmp_path: Path,
) -> None:
    _prior_gate(tmp_path)
    binding = validate_prior_gate(
        tmp_path, source_sha="a" * 40, prior_gate_run_id="123"
    )
    assert binding["workflow_run_id"] == 123
    assert binding["task_id"] == GATE_TASK_ID


@pytest.mark.parametrize(
    ("filename", "field", "value"),
    [
        ("deepswe20-attestation.json", "status", "FAIL"),
        ("deepswe20-attestation.json", "task_ids", ["other"]),
        ("deepswe20-attestation.json", "official_verifier_tasks", []),
        ("diagnostic-summary.json", "exit_code", 1),
        ("diagnostic-summary.json", "artifact_issues", ["bad"]),
    ],
)
def test_prior_gate_mutations_fail_closed(
    tmp_path: Path, filename: str, field: str, value: object
) -> None:
    _prior_gate(tmp_path)
    path = tmp_path / filename
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[field] = value
    _write(path, payload)
    with pytest.raises(ValueError):
        validate_prior_gate(tmp_path, source_sha="a" * 40, prior_gate_run_id="123")


def test_unverified_required_capability_blocks_remainder(tmp_path: Path) -> None:
    _prior_gate(tmp_path)
    path = tmp_path / "diagnostic-summary.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["capabilities"][0]["verified"] = False
    _write(path, payload)
    with pytest.raises(ValueError, match="required capability"):
        validate_prior_gate(tmp_path, source_sha="a" * 40, prior_gate_run_id="123")
