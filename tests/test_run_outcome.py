from __future__ import annotations

import json

from gt_engine.run_outcome import (
    ArtifactIntegrity,
    GraderOutcome,
    GtOutcome,
    ProcessOutcome,
    ResearchValidity,
    SolverOutcome,
    join_trial_outcome,
    summarize_outcomes,
)


def _result(reward, exception_type="", exception_message=""):
    data = {"verifier_result": {"rewards": {"reward": reward}}}
    if exception_type:
        data["exception_info"] = {
            "exception_type": exception_type,
            "exception_message": exception_message,
        }
    return data


def test_clean_submitted_reward_is_valid_and_keeps_official_reward():
    outcome = join_trial_outcome(
        _result(1.0),
        {"terminal": "submitted_unverified", "exit_code": 0, "gt": {}},
    )
    assert outcome.process_outcome is ProcessOutcome.COMPLETED
    assert outcome.solver_outcome is SolverOutcome.UNVERIFIED_SUBMISSION
    assert outcome.grader_outcome is GraderOutcome.PASS
    assert outcome.artifact_integrity is ArtifactIntegrity.COMPLETE
    assert outcome.research_validity is ResearchValidity.VALID
    assert outcome.derived_label == "CLEAN_SUBMITTED_RESOLVED"


def test_rewarded_outer_timeout_is_interrupted_not_unknown_or_healthy():
    outcome = join_trial_outcome(
        _result(1.0, "AgentTimeoutError", "timed out after 900 seconds"),
        None,
    )
    assert outcome.process_outcome is ProcessOutcome.INTERRUPTED
    assert outcome.solver_outcome is SolverOutcome.UNKNOWN
    assert outcome.grader_outcome is GraderOutcome.PASS
    assert outcome.artifact_integrity is ArtifactIntegrity.INCOMPLETE
    assert outcome.research_validity is ResearchValidity.INVALID
    assert outcome.derived_label == "INTERRUPTED_RESOLVED"


def test_gt_lifecycle_abort_is_not_model_stuck():
    outcome = join_trial_outcome(
        _result(1.0, "NonZeroAgentExitCodeError", "command exit 1"),
        {
            "terminal": "stuck",
            "exit_code": 1,
            "exception": "LifecycleError: tool action after STUCK",
            "gt": {"phase": "STUCK"},
        },
    )
    assert outcome.process_outcome is ProcessOutcome.HARNESS_ERROR
    assert outcome.solver_outcome is SolverOutcome.STUCK
    assert outcome.gt_outcome is GtOutcome.GT_ABORTED
    assert outcome.research_validity is ResearchValidity.INVALID
    assert outcome.derived_label == "GT_ABORTED_RESOLVED"


def test_normal_exhaustion_is_completed_and_gradable():
    outcome = join_trial_outcome(
        _result(1.0),
        {"terminal": "budget_exhausted", "exit_code": 0, "gt": {}},
    )
    assert outcome.process_outcome is ProcessOutcome.COMPLETED
    assert outcome.solver_outcome is SolverOutcome.EXHAUSTED
    assert outcome.research_validity is ResearchValidity.VALID
    assert outcome.derived_label == "SALVAGED_RESOLVED"


def test_nonzero_exhaustion_is_preserved_as_an_exit_contract_defect():
    outcome = join_trial_outcome(
        _result(1.0, "NonZeroAgentExitCodeError", "command exit 2"),
        {"terminal": "budget_exhausted", "exit_code": 2, "gt": {}},
    )
    assert outcome.process_outcome is ProcessOutcome.HARNESS_ERROR
    assert outcome.research_validity is ResearchValidity.INVALID
    assert outcome.derived_label == "SALVAGED_RESOLVED_WITH_EXIT_DEFECT"


def test_summary_never_hides_missing_reports():
    outcomes = [
        join_trial_outcome(
            _result(1.0),
            {"terminal": "submitted_unverified", "exit_code": 0, "gt": {}},
            task_name="clean",
        ),
        join_trial_outcome(
            _result(1.0, "AgentTimeoutError", "timeout"),
            None,
            task_name="timeout",
        ),
    ]
    summary = summarize_outcomes(outcomes)
    assert summary["official_resolved"] == 2
    assert summary["clean_submitted_resolved"] == 1
    assert summary["interrupted_resolved"] == 1
    assert summary["runner_reports_present"] == 1
    assert summary["research_valid"] is False


def test_integrity_cli_fails_missing_report_even_when_reward_is_one(tmp_path, capsys):
    from scripts.miniswe_gt_integrity import main

    trial = tmp_path / "cobol-modernization__abc"
    trial.mkdir()
    (trial / "result.json").write_text(
        json.dumps(
            {
                "config": {"task": {"path": "cobol-modernization"}},
                **_result(1.0, "AgentTimeoutError", "timeout"),
            }
        ),
        encoding="utf-8",
    )
    assert main([str(tmp_path)]) == 1
    output = capsys.readouterr().out
    assert "INTERRUPTED_RESOLVED" in output
    assert "research_valid=False" in output
