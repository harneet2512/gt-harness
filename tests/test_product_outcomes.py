from __future__ import annotations

import json
from pathlib import Path

import pytest

from gt_harness.outcomes import (
    OutcomeBindingError,
    bind_evaluator_outcome,
    bind_harbor_run_directory,
)


def _run_receipt(
    task_id: str = "task-one", *, status: str = "COMPLETED"
) -> dict[str, object]:
    return {
        "schema": "gt.run_receipt.v1",
        "run_id": "run-one",
        "task_id": task_id,
        "trial_id": "1",
        "status": status,
        "resolved": None,
        "treatment": "groundtruth",
    }


def test_outcome_binding_derives_harbor_result_and_hash_binds_receipts(
    tmp_path: Path,
) -> None:
    run_path = tmp_path / "run.json"
    evaluator_path = tmp_path / "result.json"
    output_path = tmp_path / "evaluated.json"
    run_path.write_text(json.dumps(_run_receipt()), encoding="utf-8")
    evaluator_path.write_text(
        json.dumps(
            {
                "task_name": "task-one",
                "verifier_result": {"rewards": {"tests": 1.0, "quality": 1}},
            }
        ),
        encoding="utf-8",
    )

    result = bind_evaluator_outcome(run_path, evaluator_path, output_path)

    assert result["resolved"] is True
    assert result["evaluation"]["schema"] == "gt.evaluation_binding.v1"
    assert result["evaluation"]["evaluator_format"] == "harbor"
    assert result["evaluation"]["infrastructure_disposition"] == "NONE"
    assert result["evaluation"]["official_verifier_authoritative"] is True
    assert len(result["evaluation"]["run_receipt_sha256"]) == 64
    assert len(result["evaluation"]["evaluator_receipt_sha256"]) == 64
    assert json.loads(output_path.read_text(encoding="utf-8")) == result


def test_outcome_binding_rejects_wrong_task_or_ungraded_result(tmp_path: Path) -> None:
    run_path = tmp_path / "run.json"
    evaluator_path = tmp_path / "result.json"
    output_path = tmp_path / "evaluated.json"
    run_path.write_text(json.dumps(_run_receipt()), encoding="utf-8")
    evaluator_path.write_text(
        json.dumps(
            {
                "task_name": "different-task",
                "verifier_result": {"rewards": {"tests": 1.0}},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(OutcomeBindingError, match="matching graded task"):
        bind_evaluator_outcome(run_path, evaluator_path, output_path)

    evaluator_path.write_text(
        json.dumps({"task_name": "task-one", "verifier_result": {"rewards": {}}}),
        encoding="utf-8",
    )
    with pytest.raises(OutcomeBindingError, match="matching graded task"):
        bind_evaluator_outcome(run_path, evaluator_path, output_path)


def test_outcome_binding_refuses_to_overwrite_an_existing_outcome(tmp_path: Path) -> None:
    run_path = tmp_path / "run.json"
    evaluator_path = tmp_path / "result.json"
    output_path = tmp_path / "evaluated.json"
    receipt = _run_receipt()
    receipt["resolved"] = False
    run_path.write_text(json.dumps(receipt), encoding="utf-8")
    evaluator_path.write_text(
        json.dumps(
            {
                "task_name": "task-one",
                "verifier_result": {"rewards": {"tests": 1.0}},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(OutcomeBindingError, match="already has an evaluator outcome"):
        bind_evaluator_outcome(run_path, evaluator_path, output_path)


def test_outcome_binding_rejects_nonterminal_run_status(tmp_path: Path) -> None:
    run_path = tmp_path / "run.json"
    evaluator_path = tmp_path / "result.json"
    output_path = tmp_path / "evaluated.json"
    run_path.write_text(
        json.dumps(_run_receipt(status="RUNNING")), encoding="utf-8"
    )
    evaluator_path.write_text(
        json.dumps(
            {
                "task_name": "task-one",
                "verifier_result": {"rewards": {"tests": 0.0}},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(OutcomeBindingError, match="unsupported run receipt status"):
        bind_evaluator_outcome(run_path, evaluator_path, output_path)


def test_official_verifier_preserves_typed_timeout_disposition(tmp_path: Path) -> None:
    run_path = tmp_path / "run.json"
    evaluator_path = tmp_path / "result.json"
    output_path = tmp_path / "evaluated.json"
    receipt = _run_receipt(status="ERROR")
    receipt["termination"] = {
        "schema": "gt.termination.v1",
        "kind": "TIMEOUT",
        "authority": "pier_adapter_timeout",
    }
    run_path.write_text(json.dumps(receipt), encoding="utf-8")
    evaluator_path.write_text(
        json.dumps(
            {
                "task_name": "task-one",
                "verifier_result": {"rewards": {"tests": 0.0}},
            }
        ),
        encoding="utf-8",
    )

    result = bind_evaluator_outcome(run_path, evaluator_path, output_path)

    assert result["resolved"] is False
    assert result["evaluation"]["infrastructure_disposition"] == "ORCHESTRATOR_TIMEOUT"
    assert result["evaluation"]["termination_kind"] == "TIMEOUT"


def test_record_outcome_cli_exercises_the_public_product_boundary(
    tmp_path: Path, capsys
) -> None:
    from gt_harness.cli import main

    run_path = tmp_path / "run.json"
    evaluator_path = tmp_path / "result.json"
    output_path = tmp_path / "evaluated.json"
    run_path.write_text(json.dumps(_run_receipt()), encoding="utf-8")
    evaluator_path.write_text(
        json.dumps(
            {
                "task_name": "task-one",
                "verifier_result": {"rewards": {"tests": 0.0}},
            }
        ),
        encoding="utf-8",
    )

    assert main(
        [
            "record-outcome",
            "--run-receipt",
            str(run_path),
            "--evaluator-receipt",
            str(evaluator_path),
            "--output",
            str(output_path),
        ]
    ) == 0
    emitted = json.loads(capsys.readouterr().out)
    assert emitted["status"] == "BOUND"
    assert emitted["resolved"] is False
    assert json.loads(output_path.read_text(encoding="utf-8"))["resolved"] is False


def test_harbor_directory_binding_produces_comparison_ready_receipts(
    tmp_path: Path,
) -> None:
    trial = tmp_path / "harbor" / "task-one__abc1234"
    agent = trial / "agent"
    agent.mkdir(parents=True)
    (agent / "gt-run.json").write_text(json.dumps(_run_receipt()), encoding="utf-8")
    (trial / "result.json").write_text(
        json.dumps(
            {
                "task_name": "task-one",
                "verifier_result": {"rewards": {"tests": 1.0}},
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "evaluated"

    summary = bind_harbor_run_directory(tmp_path / "harbor", output)

    assert summary["status"] == "COMPLETE"
    assert summary["bound_receipts"] == 1
    receipts = list(output.glob("*.json"))
    assert len(receipts) == 1
    assert json.loads(receipts[0].read_text(encoding="utf-8"))["resolved"] is True


def test_harbor_directory_binding_accepts_harbor_020_structured_task_id(
    tmp_path: Path,
) -> None:
    """Harbor 0.20 emits task_id as provenance, not a scalar task name."""

    trial = tmp_path / "harbor" / "task-one__abc1234"
    agent = trial / "agent"
    agent.mkdir(parents=True)
    (agent / "gt-run.json").write_text(json.dumps(_run_receipt()), encoding="utf-8")
    (trial / "result.json").write_text(
        json.dumps(
            {
                "task_name": "task-one",
                "trial_name": "task-one__abc1234",
                "task_id": {
                    "git_url": "https://example.invalid/tasks.git",
                    "git_commit_id": "a" * 40,
                    "path": "task-one",
                },
                "verifier_result": {"rewards": {"reward": 1.0}},
            }
        ),
        encoding="utf-8",
    )

    summary = bind_harbor_run_directory(
        tmp_path / "harbor", tmp_path / "evaluated"
    )

    assert summary["status"] == "COMPLETE"
    assert summary["bound_receipts"] == 1


def test_harbor_directory_binding_rejects_incomplete_runs(
    tmp_path: Path,
) -> None:
    trial = tmp_path / "harbor" / "task-one__abc1234"
    agent = trial / "agent"
    agent.mkdir(parents=True)
    (agent / "gt-run.json").write_text(
        json.dumps(_run_receipt(status="RUNNING")), encoding="utf-8"
    )
    (trial / "result.json").write_text(
        json.dumps(
            {
                "task_name": "task-one",
                "verifier_result": {"rewards": {"tests": 0.0}},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(OutcomeBindingError, match="unsupported run receipt status"):
        bind_harbor_run_directory(tmp_path / "harbor", tmp_path / "evaluated")
