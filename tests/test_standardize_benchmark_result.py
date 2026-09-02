from __future__ import annotations

import json
import sys
from pathlib import Path

from scripts.standardize_benchmark_result import (
    conservative_outcomes,
    main,
    standardize_result,
)


def _write_case(
    root: Path,
    *,
    aggregate: dict[str, object],
    trial: dict[str, object],
    product: bool = True,
) -> Path:
    job = root / "job"
    trial_dir = job / "task-a__trial"
    agent = trial_dir / "agent"
    agent.mkdir(parents=True)
    (job / "result.json").write_text(json.dumps(aggregate), encoding="utf-8")
    (trial_dir / "result.json").write_text(json.dumps(trial), encoding="utf-8")
    if product:
        (agent / "gt-run.json").write_text(
            json.dumps({"schema": "gt.run_receipt.v1", "task_id": "task-a"}),
            encoding="utf-8",
        )
    return agent


def _standardize(root: Path) -> dict[str, object]:
    return standardize_result(
        root=root,
        suite="deepswe",
        task_id="task-a",
        source_sha="a" * 40,
    )


def test_standardize_success_binds_only_official_reward(tmp_path: Path) -> None:
    agent = _write_case(
        tmp_path,
        aggregate={
            "n_total_trials": 1,
            "stats": {"evals": {"task": {"metrics": [{"reward": 1.0}]}}},
        },
        trial={
            "task_name": "datacurve/task-a",
            "trial_name": "task-a__trial",
            "verifier_result": {"rewards": {"reward": 1}},
        },
    )

    receipt = _standardize(tmp_path)

    assert receipt["status"] == "GRADED"
    assert receipt["reward"] == 1
    assert receipt["failure_class"] == "graded"
    assert receipt["product_receipt_present"] is True
    assert (agent / "official-verifier-result.json").is_file()


def test_standardize_provider_balance_failure_is_not_rate_limit(tmp_path: Path) -> None:
    agent = _write_case(
        tmp_path,
        aggregate={"n_total_trials": 1, "stats": {"evals": {}}},
        trial={
            "task_name": "datacurve/task-a",
            "trial_name": "task-a__trial",
            "exception_info": {
                "exception_type": "ApiRateLimitError",
                "exception_message": "OpenAIException - Insufficient Balance",
            },
        },
        product=False,
    )

    receipt = _standardize(tmp_path)

    assert receipt["status"] == "ERROR"
    assert receipt["reward"] is None
    assert receipt["failure_class"] == "provider_billing_failure"
    assert receipt["error_code"] == "provider_insufficient_balance"
    assert receipt["product_receipt_present"] is False
    assert (agent / "official-verifier-result.json").is_file()


def test_standardize_setup_failure_without_runner_output_remains_ungraded(
    tmp_path: Path,
) -> None:
    receipt = _standardize(tmp_path)

    assert receipt["status"] == "ERROR"
    assert receipt["reward"] is None
    assert receipt["failure_class"] == "setup_failure"
    assert receipt["error_code"] == "runner_result_missing"
    assert receipt["runner_result_sha256"] is None
    assert receipt["runner_result_path"] is None
    assert (
        tmp_path / "task-a" / "agent" / "official-verifier-result.json"
    ).is_file()


def test_standardize_missing_verifier_never_manufactures_reward(tmp_path: Path) -> None:
    _write_case(
        tmp_path,
        aggregate={"n_total_trials": 1, "stats": {"evals": {}}},
        trial={"task_name": "datacurve/task-a", "trial_name": "task-a__trial"},
    )

    receipt = _standardize(tmp_path)

    assert receipt["status"] == "ERROR"
    assert receipt["reward"] is None
    assert receipt["solved"] is None
    assert receipt["failure_class"] == "missing_verifier"
    assert receipt["error_code"] == "official_verifier_missing"


def test_standardize_boolean_reward_cannot_manufacture_grade(tmp_path: Path) -> None:
    _write_case(
        tmp_path,
        aggregate={
            "n_total_trials": 1,
            "stats": {"evals": {"task": {"metrics": [{"reward": True}]}}},
        },
        trial={
            "task_name": "datacurve/task-a",
            "trial_name": "task-a__trial",
            "verifier_result": {"rewards": {"reward": 1}},
        },
    )

    receipt = _standardize(tmp_path)

    assert receipt["status"] == "ERROR"
    assert receipt["reward"] is None
    assert receipt["solved"] is None


def test_standardize_malformed_nested_reward_evidence_stays_ungraded(
    tmp_path: Path,
) -> None:
    _write_case(
        tmp_path,
        aggregate={
            "n_total_trials": 1,
            "verifier_result": [],
            "rewards": [],
            "stats": [],
        },
        trial={
            "task_name": "datacurve/task-a",
            "trial_name": "task-a__trial",
            "exception_info": [],
        },
    )

    receipt = _standardize(tmp_path)

    assert receipt["status"] == "ERROR"
    assert receipt["reward"] is None
    assert receipt["solved"] is None


def test_ambiguous_results_write_typed_error_receipt(
    tmp_path: Path, monkeypatch
) -> None:
    _write_case(
        tmp_path,
        aggregate={"n_total_trials": 1, "stats": {"evals": {}}},
        trial={"task_name": "datacurve/task-a", "trial_name": "task-a__trial"},
    )
    extra = tmp_path / "other" / "result.json"
    extra.parent.mkdir()
    extra.write_text(json.dumps({"n_total_trials": 1, "stats": {}}))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "standardize_benchmark_result",
            "--root", str(tmp_path),
            "--suite", "deepswe",
            "--task-id", "task-a",
            "--source-sha", "a" * 40,
        ],
    )

    exit_code = main()

    receipt_path = tmp_path / "task-a" / "agent" / "official-verifier-result.json"
    receipt = json.loads(receipt_path.read_text())
    assert exit_code == 1
    assert receipt["status"] == "ERROR"
    assert receipt["error_code"] == "official_verifier_construction_failed"
    assert receipt["product_receipt_present"] is True


def test_conservative_outcomes_represents_missing_task_once() -> None:
    outcomes = conservative_outcomes(
        ["task-a", "task-b"],
        {
            "task-a": {
                "status": "GRADED",
                "reward": 1,
                "failure_class": "graded",
                "error_code": "",
            }
        },
    )

    assert list(outcomes) == ["task-a", "task-b"]
    assert outcomes["task-a"]["solved"] is True
    assert outcomes["task-b"] == {
        "status": "ERROR",
        "graded": False,
        "reward": None,
        "solved": False,
        "failure_class": "missing_result",
        "error_code": "official_verifier_result_missing",
    }


def test_conservative_outcomes_rejects_boolean_reward() -> None:
    outcomes = conservative_outcomes(
        ["task-a"],
        {"task-a": {"status": "GRADED", "reward": True}},
    )

    assert outcomes["task-a"]["graded"] is False
    assert outcomes["task-a"]["solved"] is False
