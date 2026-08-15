from __future__ import annotations

import pytest

from gt_engine.experiment import (
    BASELINE_ACTIONS,
    BASELINE_SOLVED,
    BASELINE_TOKENS,
    ExperimentArm,
    RepeatedReleaseCriteria,
    TrialRecord,
    assess_release,
    assess_repeated_release,
    crossover_arm,
    deterministic_arm,
    select_eligible_panel,
)


def test_assignment_is_stable_and_uses_both_gt_on_arms():
    first = deterministic_arm("task-a", 1, "lint", "v1", "seed")
    second = deterministic_arm("task-a", 1, "lint", "v1", "seed")
    arms = {
        deterministic_arm(f"task-{i}", 1, "lint", "v1", "seed")
        for i in range(100)
    }

    assert first == second
    assert arms == {"shadow", "treatment"}


def test_crossover_assignment_is_balanced_within_every_four_rounds():
    for task in ("scheme", "cobol", "portfolio"):
        assignments = [
            crossover_arm(task, round_index, seed="release-v1")
            for round_index in range(4)
        ]
        assert assignments.count(ExperimentArm.OFF) == 2
        assert assignments.count(ExperimentArm.CERTIFIED_FULL) == 2
        assert assignments in (
            [
                ExperimentArm.OFF,
                ExperimentArm.CERTIFIED_FULL,
                ExperimentArm.CERTIFIED_FULL,
                ExperimentArm.OFF,
            ],
            [
                ExperimentArm.CERTIFIED_FULL,
                ExperimentArm.OFF,
                ExperimentArm.OFF,
                ExperimentArm.CERTIFIED_FULL,
            ],
        )


def test_repeated_release_uses_fresh_controls_and_caps_failed_costs():
    records: list[TrialRecord] = []
    for task in ("a", "b", "c", "d"):
        for trial in range(4):
            arm = ExperimentArm.OFF if trial < 2 else ExperimentArm.CERTIFIED_FULL
            solved = not (arm is ExperimentArm.OFF and task in {"c", "d"})
            records.append(
                TrialRecord(
                    task=task,
                    trial=trial,
                    arm=arm.value,
                    solved=solved,
                    tokens=50 if solved else 10,
                    actions=5 if solved else 1,
                    calls=5 if solved else 1,
                    steps=5 if solved else 1,
                    effective_actions=6 if solved else 1,
                    wall_time_sec=20 if solved else 2,
                    token_cap=100,
                    action_cap=10,
                    call_cap=10,
                    step_cap=10,
                    effective_action_cap=12,
                    wall_time_cap_sec=60,
                )
            )

    result = assess_repeated_release(
        records,
        criteria=RepeatedReleaseCriteria(
            require_positive_uplift=False,
            noninferiority_margin=0.0,
            maximum_resource_ratio=1.0,
        ),
        bootstrap_samples=2_000,
        seed=11,
    )

    assert result.off_trials == result.treatment_trials == 8
    assert result.treatment_only_solves == 4
    assert result.control_only_solves == 0
    assert result.capped_token_ratio < 1.0
    assert result.passed is True


def test_repeated_release_rejects_single_frozen_control_and_treatment_regression():
    records = [
        TrialRecord("a", 0, ExperimentArm.OFF.value, True, 50, 5),
        TrialRecord(
            "a",
            1,
            ExperimentArm.CERTIFIED_FULL.value,
            False,
            10,
            1,
            token_cap=100,
            action_cap=10,
            call_cap=10,
            step_cap=10,
            effective_action_cap=10,
            wall_time_cap_sec=60,
        ),
        TrialRecord("b", 0, ExperimentArm.OFF.value, True, 50, 5),
        TrialRecord("b", 1, ExperimentArm.CERTIFIED_FULL.value, True, 80, 8),
    ]

    with pytest.raises(ValueError, match="at least two trials per arm"):
        assess_repeated_release(records, bootstrap_samples=100)

    repeated = records + [
        TrialRecord("a", 2, ExperimentArm.OFF.value, True, 50, 5),
        TrialRecord(
            "a",
            3,
            ExperimentArm.CERTIFIED_FULL.value,
            False,
            10,
            1,
            token_cap=100,
            action_cap=10,
            call_cap=10,
            step_cap=10,
            effective_action_cap=10,
            wall_time_cap_sec=60,
        ),
        TrialRecord("b", 2, ExperimentArm.OFF.value, True, 50, 5),
        TrialRecord("b", 3, ExperimentArm.CERTIFIED_FULL.value, True, 80, 8),
    ]
    result = assess_repeated_release(repeated, bootstrap_samples=500, seed=3)

    assert result.passed is False
    assert "solve_rate" in result.failures


def test_repeated_release_rejects_uncapped_cheap_failure():
    records = [
        TrialRecord("a", 0, ExperimentArm.OFF.value, True, 50, 5),
        TrialRecord("a", 1, ExperimentArm.OFF.value, True, 50, 5),
        TrialRecord("a", 2, ExperimentArm.CERTIFIED_FULL.value, False, 1, 1),
        TrialRecord("a", 3, ExperimentArm.CERTIFIED_FULL.value, True, 50, 5),
    ]

    with pytest.raises(ValueError, match="unsolved trials require every resource cap"):
        assess_repeated_release(records, bootstrap_samples=100)


def test_eligible_panel_is_deterministic_and_severity_ranked():
    events = {f"task-{i:02d}": float(i) for i in range(40)}

    panel = select_eligible_panel(events, minimum=20, maximum=30)

    assert len(panel) == 30
    assert panel[0] == "task-39"
    assert panel[-1] == "task-10"


def test_too_few_eligible_tasks_stays_shadow_only():
    assert select_eligible_panel({"a": 2.0, "b": 1.0}, minimum=20) == ()


def test_release_gate_accepts_clear_pareto_improvement():
    baseline = {
        f"task-{i:02d}": TrialRecord(
            task=f"task-{i:02d}",
            trial=0,
            arm="baseline",
            solved=i < BASELINE_SOLVED,
            tokens=BASELINE_TOKENS / 89,
            actions=BASELINE_ACTIONS / 89,
            errored=i < 4,
        )
        for i in range(89)
    }
    candidate = [
        TrialRecord(
            task=f"task-{i:02d}",
            trial=trial,
            arm="treatment",
            solved=i < 75,
            tokens=(BASELINE_TOKENS * 0.70) / 89,
            actions=(BASELINE_ACTIONS * 0.70) / 89,
            errored=False,
        )
        for trial in range(1, 6)
        for i in range(89)
    ]

    result = assess_release(
        baseline,
        candidate,
        bootstrap_samples=2_000,
        seed=7,
        runtime_errors=0,
        permanently_blocked_submissions=0,
    )

    assert result.passed is True
    assert result.mean_solved >= 72
    assert result.mean_tokens <= BASELINE_TOKENS * 0.85
    assert result.mean_actions <= BASELINE_ACTIONS * 0.85


def test_release_gate_rejects_efficiency_or_reliability_regression():
    baseline = {
        f"task-{i:02d}": TrialRecord(
            task=f"task-{i:02d}",
            trial=0,
            arm="baseline",
            solved=i < BASELINE_SOLVED,
            tokens=BASELINE_TOKENS / 89,
            actions=BASELINE_ACTIONS / 89,
            errored=i < 4,
        )
        for i in range(89)
    }
    candidate = [
        TrialRecord(
            task=f"task-{i:02d}",
            trial=trial,
            arm="treatment",
            solved=i < 75,
            tokens=(BASELINE_TOKENS * 1.10) / 89,
            actions=(BASELINE_ACTIONS * 1.10) / 89,
            errored=i < 5,
        )
        for trial in range(1, 6)
        for i in range(89)
    ]

    result = assess_release(baseline, candidate, bootstrap_samples=500, seed=9)

    assert result.passed is False
    assert "tokens" in result.failures
    assert "actions" in result.failures
    assert "errors" in result.failures


def test_release_gate_rejects_duplicate_trial_ids_within_a_task():
    baseline = {
        f"task-{i:02d}": TrialRecord(
            task=f"task-{i:02d}",
            trial=0,
            arm="baseline",
            solved=i < BASELINE_SOLVED,
            tokens=BASELINE_TOKENS / 89,
            actions=BASELINE_ACTIONS / 89,
        )
        for i in range(89)
    }
    candidate = [
        TrialRecord(
            task=f"task-{i:02d}",
            trial=(1 if i == 0 and trial == 2 else trial),
            arm="treatment",
            solved=i < 75,
            tokens=(BASELINE_TOKENS * 0.70) / 89,
            actions=(BASELINE_ACTIONS * 0.70) / 89,
        )
        for trial in range(1, 6)
        for i in range(89)
    ]

    with pytest.raises(ValueError, match="same five unique trials"):
        assess_release(baseline, candidate, bootstrap_samples=100)
