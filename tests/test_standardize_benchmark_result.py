from __future__ import annotations

import json
from pathlib import Path

from scripts.standardize_benchmark_result import conservative_outcomes, standardize_result


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
        trial={"task_name": "datacurve/task-a", "trial_name": "task-a__trial"},
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
