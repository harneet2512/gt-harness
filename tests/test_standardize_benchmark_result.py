from __future__ import annotations

import hashlib
import hmac
import json
import sys
from pathlib import Path

import pytest

from scripts.standardize_benchmark_result import (
    _PRODUCT_TERMINAL_FAILURES,
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
    terminal: str | None = None,
) -> Path:
    job = root / "job"
    trial_dir = job / "task-a__trial"
    agent = trial_dir / "agent"
    agent.mkdir(parents=True)
    (job / "result.json").write_text(json.dumps(aggregate), encoding="utf-8")
    (trial_dir / "result.json").write_text(json.dumps(trial), encoding="utf-8")
    if product:
        receipt = {"schema": "gt.run_receipt.v1", "task_id": "task-a",
                   "product_source_sha": "a" * 40}
        if terminal is not None:
            receipt["terminal"] = terminal
        (agent / "gt-run.json").write_text(
            json.dumps(receipt), encoding="utf-8",
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
    # The aggregate uses pier's real production shape: metrics carry {"mean"}
    # aggregates and the per-reward trial distribution lives in
    # reward_stats.reward. A metrics[{"reward": x}] fixture is a shape pier
    # never emits - it is what let run 34790375793's real solve read as
    # missing_verifier.
    agent = _write_case(
        tmp_path,
        aggregate={
            "n_total_trials": 1,
            "stats": {
                "evals": {
                    "task": {
                        "metrics": [{"mean": 1.0}],
                        "reward_stats": {
                            "reward": {"1.0": ["task-a__trial"]}
                        },
                    }
                }
            },
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


def test_standardize_reward_stats_disagreement_stays_ungraded(
    tmp_path: Path,
) -> None:
    # The aggregate and the trial disagreeing is contradictory evidence, not
    # a grade: aggregate says 0.0, trial says 1. The run must stay ungraded
    # rather than picking a side.
    _write_case(
        tmp_path,
        aggregate={
            "n_total_trials": 1,
            "stats": {
                "evals": {
                    "task": {
                        "metrics": [{"mean": 0.0}],
                        "reward_stats": {
                            "reward": {"0.0": ["task-a__trial"]}
                        },
                    }
                }
            },
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


def test_standardize_mixed_reward_map_is_not_a_single_reward(
    tmp_path: Path,
) -> None:
    # A multi-key reward_stats.reward map describes trials with DIFFERENT
    # outcomes; collapsing it to one value would manufacture a grade the
    # aggregate never stated.
    _write_case(
        tmp_path,
        aggregate={
            "n_total_trials": 2,
            "stats": {
                "evals": {
                    "task": {
                        "metrics": [{"mean": 0.5}],
                        "reward_stats": {
                            "reward": {
                                "0.0": ["task-a__t1"],
                                "1.0": ["task-a__t2"],
                            }
                        },
                    }
                }
            },
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


def test_churn_abort_terminal_is_governor_abort_not_setup_failure(tmp_path: Path) -> None:
    """A deliberate runtime-governor abort must not read as runner failure.

    adaptix in run 34701523365 ended terminal=churn_abort (exit 7) and the
    generic NonZeroAgentExitCodeError collapse reported it as
    runner_setup_or_execution_failed -- erasing the typed outcome.
    """
    _write_case(
        tmp_path,
        aggregate={"n_total_trials": 1, "stats": {"evals": {}}},
        trial={
            "task_name": "datacurve/task-a",
            "trial_name": "task-a__trial",
            "exception_info": {
                "exception_type": "NonZeroAgentExitCodeError",
                "exception_message": "Command failed (exit 7)",
            },
        },
        terminal="churn_abort",
    )

    receipt = _standardize(tmp_path)

    assert receipt["status"] == "ERROR"
    assert receipt["reward"] is None
    assert receipt["failure_class"] == "governor_abort"
    assert receipt["error_code"] == "churn_abort"


def test_specific_exception_evidence_still_beats_the_terminal(tmp_path: Path) -> None:
    """The terminal labels the outcome; exception evidence names the cause."""
    _write_case(
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
        terminal="provider_failed",
    )

    receipt = _standardize(tmp_path)

    assert receipt["failure_class"] == "provider_billing_failure"
    assert receipt["error_code"] == "provider_insufficient_balance"


@pytest.mark.parametrize(
    ("terminal", "failure_class", "error_code"),
    sorted(
        (terminal, failure_class, error_code)
        for terminal, (failure_class, error_code)
        in _PRODUCT_TERMINAL_FAILURES.items()
    ),
)
def test_every_product_terminal_maps_to_its_typed_outcome(
    tmp_path: Path, terminal: str, failure_class: str, error_code: str
) -> None:
    """The terminal x stage matrix: every supervisor terminal must standardize
    to its declared class so attestation recomputes identically and no typed
    outcome collapses into runner_setup_or_execution_failed."""
    _write_case(
        tmp_path,
        aggregate={"n_total_trials": 1, "stats": {"evals": {}}},
        trial={
            "task_name": "datacurve/task-a",
            "trial_name": "task-a__trial",
            "exception_info": {
                "exception_type": "NonZeroAgentExitCodeError",
                "exception_message": "Command failed",
            },
        },
        terminal=terminal,
    )

    receipt = _standardize(tmp_path)

    assert receipt["status"] == "ERROR"
    assert receipt["failure_class"] == failure_class
    assert receipt["error_code"] == error_code


def test_unknown_or_absent_terminal_keeps_legacy_classification(tmp_path: Path) -> None:
    """Terminals outside the typed map must not rewrite recorded semantics."""
    _write_case(
        tmp_path,
        aggregate={"n_total_trials": 1, "stats": {"evals": {}}},
        trial={
            "task_name": "datacurve/task-a",
            "trial_name": "task-a__trial",
            "exception_info": {
                "exception_type": "NonZeroAgentExitCodeError",
                "exception_message": "Command failed (exit 9)",
            },
        },
        terminal="submitted_unverified",
    )

    receipt = _standardize(tmp_path)

    assert receipt["failure_class"] == "setup_failure"
    assert receipt["error_code"] == "runner_setup_or_execution_failed"


def _write_artifact_manifest(root: Path, rows: list[dict[str, object]]) -> Path:
    """The upstream pier/harbor collector's per-trial artifact manifest.

    Harbor copies each declared artifact out of the container after the agent
    phase and records the outcome of every copy here; run 35016130850's
    ``artifacts/manifest.json`` is exactly this shape with the model.patch row
    carrying ``"status": "failed"``.
    """
    artifacts = root / "job" / "task-a__trial" / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    manifest = artifacts / "manifest.json"
    manifest.write_text(json.dumps({"artifacts": rows}), encoding="utf-8")
    return manifest


def test_failed_model_patch_collection_is_typed(tmp_path: Path) -> None:
    """Run 35016130850: the patch copy failed, so nothing could be graded.

    The trial's ``verifier_result`` is null and no exception was recorded, so
    the classifier fell all the way through to ``missing_verifier`` /
    ``official_verifier_missing`` -- a sentence about the verifier being absent
    for a run whose verifier was never given anything to grade. The manifest
    beside the result names the real failure, and the receipt must say so.
    """
    _write_case(
        tmp_path,
        aggregate={"n_total_trials": 1, "stats": {"evals": {}}},
        trial={
            "task_name": "datacurve/task-a",
            "trial_name": "task-a__trial",
            "verifier_result": None,
            "exception_info": None,
        },
    )
    _write_artifact_manifest(
        tmp_path,
        [{"source": "/logs/artifacts/model.patch", "status": "failed"}],
    )

    receipt = _standardize(tmp_path)

    assert receipt["status"] == "ERROR"
    assert receipt["error_code"] == "model_patch_copy_failed"
    assert receipt["failure_class"] == "artifact_collection_failure"
    assert receipt["artifact_collection"] == {
        "artifact": "model.patch",
        "source": "/logs/artifacts/model.patch",
        "status": "failed",
        "manifest_path": "job/task-a__trial/artifacts/manifest.json",
    }
    # Nothing graded, and no verifier outcome to preserve.
    assert receipt["reward"] is None
    assert receipt["verifier_outcome"] is None


def test_failed_patch_collection_preserves_a_present_verifier_outcome(
    tmp_path: Path,
) -> None:
    """A verifier that answered is recorded beside the collection failure.

    The two are different facts: the copy failed, AND the verifier reported
    something that is not a usable reward. Overwriting one with the other is
    how the collection failure disappeared in the first place.
    """
    _write_case(
        tmp_path,
        aggregate={"n_total_trials": 1, "stats": {"evals": {}}},
        trial={
            "task_name": "datacurve/task-a",
            "trial_name": "task-a__trial",
            "verifier_result": {"status": "no_patch", "rewards": {}},
            "exception_info": None,
        },
    )
    _write_artifact_manifest(
        tmp_path,
        [
            {"source": "/logs/trajectory.json", "status": "ok"},
            {"source": "/logs/artifacts/model.patch", "status": "failed"},
        ],
    )

    receipt = _standardize(tmp_path)

    assert receipt["error_code"] == "model_patch_copy_failed"
    assert receipt["failure_class"] == "artifact_collection_failure"
    assert receipt["verifier_outcome"] == {
        "present": True,
        "status": "no_patch",
        "reward": None,
    }


def test_successful_patch_collection_keeps_missing_verifier(tmp_path: Path) -> None:
    """A manifest whose model.patch row succeeded changes nothing."""
    _write_case(
        tmp_path,
        aggregate={"n_total_trials": 1, "stats": {"evals": {}}},
        trial={
            "task_name": "datacurve/task-a",
            "trial_name": "task-a__trial",
            "verifier_result": None,
            "exception_info": None,
        },
    )
    _write_artifact_manifest(
        tmp_path,
        [{"source": "/logs/artifacts/model.patch", "status": "ok"}],
    )

    receipt = _standardize(tmp_path)

    assert receipt["failure_class"] == "missing_verifier"
    assert receipt["error_code"] == "official_verifier_missing"
    assert receipt["artifact_collection"] is None


def test_absent_artifact_manifest_keeps_todays_classification(
    tmp_path: Path,
) -> None:
    """No manifest is no evidence: the legacy collapse stands."""
    _write_case(
        tmp_path,
        aggregate={"n_total_trials": 1, "stats": {"evals": {}}},
        trial={
            "task_name": "datacurve/task-a",
            "trial_name": "task-a__trial",
            "verifier_result": None,
            "exception_info": None,
        },
    )

    receipt = _standardize(tmp_path)

    assert receipt["failure_class"] == "missing_verifier"
    assert receipt["error_code"] == "official_verifier_missing"
    assert receipt["artifact_collection"] is None


def test_typed_exception_evidence_still_outranks_the_manifest(
    tmp_path: Path,
) -> None:
    """The manifest replaces the generic collapse, never the named cause.

    A provider failure explains why there was no patch to copy; reporting the
    copy instead would name the symptom and lose the cause.
    """
    _write_case(
        tmp_path,
        aggregate={"n_total_trials": 1, "stats": {"evals": {}}},
        trial={
            "task_name": "datacurve/task-a",
            "trial_name": "task-a__trial",
            "verifier_result": None,
            "exception_info": {
                "exception_type": "APIConnectionError",
                "exception_message": "connection reset",
            },
        },
    )
    _write_artifact_manifest(
        tmp_path,
        [{"source": "/logs/artifacts/model.patch", "status": "failed"}],
    )

    receipt = _standardize(tmp_path)

    assert receipt["failure_class"] == "provider_failure"
    assert receipt["error_code"] == "provider_request_failed"
    # The collection failure is still recorded as evidence, just not as the
    # headline classification.
    assert receipt["artifact_collection"]["status"] == "failed"
