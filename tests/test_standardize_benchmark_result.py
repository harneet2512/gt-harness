from __future__ import annotations

import hashlib
import hmac
import json
import sys
from pathlib import Path

import pytest

from scripts.standardize_benchmark_result import (
    conservative_outcomes,
    main,
    standardize_result,
)


@pytest.fixture(autouse=True)
def _host_attestation_key(monkeypatch):
    monkeypatch.setenv("GT_RESOURCE_ATTESTATION_KEY", "f" * 64)


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
            json.dumps({"schema": "gt.run_receipt.v1", "task_id": "task-a",
                        "product_source_sha": "a" * 40}),
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


@pytest.mark.parametrize(
    "mutation",
    [
        {"schema": "wrong.schema"},
        {"product_source_sha": "b" * 40},
    ],
)
def test_product_receipt_identity_mutation_cannot_be_accepted(
    tmp_path: Path, mutation: dict[str, object]
) -> None:
    agent = _write_case(
        tmp_path,
        aggregate={"n_total_trials": 1, "stats": {"evals": {}}},
        trial={"task_name": "datacurve/task-a", "trial_name": "task-a__trial"},
    )
    path = agent / "gt-run.json"
    product = json.loads(path.read_text(encoding="utf-8"))
    product.update(mutation)
    path.write_text(json.dumps(product), encoding="utf-8")

    with pytest.raises(ValueError, match="product receipt identity"):
        _standardize(tmp_path)


def _write_resource_evidence(agent: Path, **overrides: object) -> Path:
    payload: dict[str, object] = {
        "schema": "gt.agent_resource.v1",
        "attestation_scope": "host_agent_adapter",
        "error_code": "GT_AGENT_CGROUP_OOM",
        "exit_code": 137,
        "memory_evidence": True,
        "cgroup_before": {"oom": 2, "oom_kill": 1},
        "cgroup_after": {"oom": 3, "oom_kill": 2},
        "cgroup_oom_delta": 1,
        "cgroup_oom_kill_delta": 1,
        "task_id": "task-a",
        "product_source_sha": "a" * 40,
    }
    payload.update(overrides)
    payload["attestation_hmac_sha256"] = hmac.new(
        bytes.fromhex("f" * 64),
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
        hashlib.sha256,
    ).hexdigest()
    payload["evidence_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    path = agent / "agent-resource.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_exit_137_requires_memory_evidence_before_oom_classification(tmp_path: Path) -> None:
    _write_case(
        tmp_path,
        aggregate={"n_total_trials": 1, "stats": {"evals": {}}},
        trial={
            "task_name": "datacurve/task-a",
            "trial_name": "task-a__trial",
            "exception_info": {
                "exception_type": "NonZeroAgentExitCodeError",
                "exception_message": "Command failed (exit 137)",
            },
        },
        product=False,
    )

    receipt = _standardize(tmp_path)

    assert receipt["failure_class"] == "process_signal_failure"
    assert receipt["error_code"] == "process_exit_137_unattributed"


def test_exit_137_with_sealed_memory_evidence_is_resource_exhaustion(tmp_path: Path) -> None:
    agent = _write_case(
        tmp_path,
        aggregate={"n_total_trials": 1, "stats": {"evals": {}}},
        trial={
            "task_name": "datacurve/task-a",
            "trial_name": "task-a__trial",
            "exception_info": {
                "exception_type": "NonZeroAgentExitCodeError",
                "exception_message": "Command failed (exit 137)",
            },
        },
        product=False,
    )
    evidence = _write_resource_evidence(agent)

    receipt = _standardize(tmp_path)

    assert receipt["failure_class"] == "resource_exhaustion"
    assert receipt["error_code"] == "agent_cgroup_oom"
    assert receipt["resource_evidence_path"] == str(evidence.relative_to(tmp_path)).replace("\\", "/")
    assert receipt["resource_evidence_sha256"] == hashlib.sha256(evidence.read_bytes()).hexdigest()


def test_tampered_memory_evidence_does_not_authorize_oom_classification(tmp_path: Path) -> None:
    agent = _write_case(
        tmp_path,
        aggregate={"n_total_trials": 1, "stats": {"evals": {}}},
        trial={
            "task_name": "datacurve/task-a",
            "trial_name": "task-a__trial",
            "exception_info": {
                "exception_type": "NonZeroAgentExitCodeError",
                "exception_message": "Command failed (exit 137)",
            },
        },
        product=False,
    )
    evidence = _write_resource_evidence(agent)
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    payload["cgroup_oom_kill_delta"] = 99
    evidence.write_text(json.dumps(payload), encoding="utf-8")

    receipt = _standardize(tmp_path)

    assert receipt["failure_class"] == "process_signal_failure"
    assert receipt["error_code"] == "process_exit_137_unattributed"
    assert receipt["resource_evidence_path"] is None


def test_self_digest_without_host_hmac_cannot_classify_exit_137(tmp_path: Path) -> None:
    agent = _write_case(
        tmp_path,
        aggregate={"n_total_trials": 1, "stats": {"evals": {}}},
        trial={
            "task_name": "datacurve/task-a",
            "trial_name": "task-a__trial",
            "exception_info": {
                "exception_type": "NonZeroAgentExitCodeError",
                "exception_message": "Command failed (exit 137)",
            },
        },
        product=False,
    )
    path = _write_resource_evidence(agent)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["attestation_hmac_sha256"] = "0" * 64
    payload.pop("evidence_sha256")
    payload["evidence_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    path.write_text(json.dumps(payload), encoding="utf-8")

    receipt = _standardize(tmp_path)
    assert receipt["error_code"] == "process_exit_137_unattributed"
    assert receipt["resource_evidence_path"] is None


def test_foreign_or_unbound_memory_evidence_cannot_classify_exit_137(tmp_path: Path) -> None:
    agent = _write_case(
        tmp_path,
        aggregate={"n_total_trials": 1, "stats": {"evals": {}}},
        trial={
            "task_name": "datacurve/task-a",
            "trial_name": "task-a__trial",
            "exception_info": {
                "exception_type": "NonZeroAgentExitCodeError",
                "exception_message": "Command failed (exit 137)",
            },
        },
        product=False,
    )
    _write_resource_evidence(agent, task_id="")

    receipt = _standardize(tmp_path)

    assert receipt["failure_class"] == "process_signal_failure"
    assert receipt["error_code"] == "process_exit_137_unattributed"
    assert receipt["resource_evidence_path"] is None


def test_prelaunch_headroom_refusal_cannot_explain_later_exit_137(tmp_path: Path) -> None:
    agent = _write_case(
        tmp_path,
        aggregate={"n_total_trials": 1, "stats": {"evals": {}}},
        trial={
            "task_name": "datacurve/task-a",
            "trial_name": "task-a__trial",
            "exception_info": {
                "exception_type": "NonZeroAgentExitCodeError",
                "exception_message": "Command failed (exit 137)",
            },
        },
        product=False,
    )
    _write_resource_evidence(
        agent,
        error_code="GT_AGENT_EXIT_137_UNATTRIBUTED",
        exit_code=None,
        memory_evidence=False,
    )

    receipt = _standardize(tmp_path)

    assert receipt["failure_class"] == "process_signal_failure"
    assert receipt["error_code"] == "process_exit_137_unattributed"


@pytest.mark.parametrize(
    "override",
    [
        {"task_id": "task-foreign"},
        {"product_source_sha": "b" * 40},
        {"exit_code": -9},
        {"cgroup_oom_delta": 99},
    ],
)
def test_memory_evidence_identity_mutations_fail_closed(
    tmp_path: Path, override: dict[str, object]
) -> None:
    agent = _write_case(
        tmp_path,
        aggregate={"n_total_trials": 1, "stats": {"evals": {}}},
        trial={
            "task_name": "datacurve/task-a",
            "trial_name": "task-a__trial",
            "exception_info": {
                "exception_type": "NonZeroAgentExitCodeError",
                "exception_message": "Command failed (exit 137)",
            },
        },
        product=False,
    )
    _write_resource_evidence(agent, **override)

    receipt = _standardize(tmp_path)

    assert receipt["failure_class"] == "process_signal_failure"
    assert receipt["error_code"] == "process_exit_137_unattributed"


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


def test_empty_task_identity_writes_no_official_artifact(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "standardize_benchmark_result",
            "--root", str(tmp_path),
            "--suite", "deepswe",
            "--task-id", "",
            "--source-sha", "a" * 40,
        ],
    )
    assert main() == 2
    assert not list(tmp_path.rglob("official-verifier-result.json"))
    with pytest.raises(ValueError, match="task ID must be nonempty"):
        standardize_result(
            root=tmp_path, suite="deepswe", task_id="", source_sha="a" * 40
        )


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
