import math

from scripts.deepswe_release_gate import assess_deepswe_release

IDENTITY = {
    "benchmark_sha": "b" * 40,
    "model": "deepseek/deepseek-v4-flash-0731",
    "provider": "openrouter:deepseek:only:no-fallback",
    "temperature": 1.0,
    "step_limit": 300,
    "execution_budget_sec": 5400,
    "runner": "datacurve-pier==0.3.1",
    "workspace_prompt_contract": "resolved_workspace_v1",
    "provider_response_models": ["deepseek/deepseek-v4-flash-0731"],
    "provider_response_providers": ["deepseek"],
    "provider_system_fingerprints": ["fp-test"],
    "miniswe_version": "2.2.8",
    "executor_retry_policy": "provider_once_no_retry",
    "protocol_class": "matched_diagnostic",
    "rollouts_per_task": 1,
    "leaderboard_equivalent": False,
}


def _arm(rows, *, gt_commit=""):
    treatment = bool(gt_commit)
    normalized_rows = [
        {**row, "integration_mode": "active" if treatment else "off"} for row in rows
    ]
    return {
        "schema": "gt.deepswe.central.evaluation.v1.1",
        "manifest": {
            **IDENTITY,
            "gt_commit": gt_commit,
            "arm": "gt_on" if treatment else "gt_off",
            "integration_mode": "active" if treatment else "off",
            "comparison_profile": "certified_full" if treatment else "baseline",
            "claim_scope": "integrated_groundtruth_product" if treatment else "control",
        },
        "benchmark_setup_overhead": {
            "provider_calls": 1,
            "input_tokens": 10,
            "output_tokens": 5,
            "cost_usd": 0.01,
            "latency_ms": 100.0,
        },
        "rows": normalized_rows,
    }


def _row(task, solved, *, tokens, calls, actions, cost=None, exception=None):
    return {
        "task": task,
        "solved": solved,
        "rewards": {"reward": 1 if solved else 0},
        "exception": exception,
        "total_tokens": tokens,
        "provider_calls": calls,
        "decision_actions": actions,
        "effective_actions": actions,
        "assistant_steps": calls,
        "wall_time_sec": float(calls),
        "provider_cost_usd": float(calls) * 0.01 if cost is None else cost,
        "system_prompt_sha256": "system-prompt",
        "task_prompt_sha256": f"task-prompt:{task}",
        "tool_schema_sha256": "tool-schema",
        "executor_response_models": ["deepseek/deepseek-v4-flash-0731"],
        "executor_response_providers": ["deepseek"],
        "executor_system_fingerprints": ["fp-test"],
    }


def test_preservation_gate_requires_every_baseline_solve_and_no_resource_expansion():
    baseline = _arm(
        [
            _row("kept", True, tokens=120, calls=11, actions=12),
            _row("flip", False, tokens=90, calls=9, actions=10),
        ]
    )
    treatment = _arm(
        [
            _row("kept", True, tokens=90, calls=9, actions=11),
            _row("flip", False, tokens=80, calls=8, actions=9),
        ],
        gt_commit="a" * 40,
    )

    report = assess_deepswe_release(baseline, treatment, phase="preservation")

    assert report.passed is True
    assert report.losses == ()
    assert report.task_resource_deltas == {
        "total_tokens": -30.0,
        "provider_calls": -2.0,
        "decision_actions": -1.0,
        "effective_actions": -1.0,
        "assistant_steps": -2.0,
        "wall_time_sec": -2.0,
        "provider_cost_usd": -0.02,
    }
    assert report.all_in_resource_deltas == {
        "total_tokens": -15.0,
        "provider_calls": -1.0,
        "decision_actions": -1.0,
        "effective_actions": -1.0,
        "assistant_steps": -2.0,
        "wall_time_sec": -1.9,
        "provider_cost_usd": -0.01,
    }


def test_preservation_gate_rejects_one_loss_even_when_aggregate_is_lower():
    baseline = _arm(
        [
            _row("lost", True, tokens=1000, calls=50, actions=60),
            _row("kept", True, tokens=1000, calls=50, actions=60),
        ]
    )
    treatment = _arm(
        [
            _row("lost", False, tokens=1, calls=1, actions=1),
            _row("kept", True, tokens=10, calls=2, actions=3),
        ],
        gt_commit="a" * 40,
    )

    report = assess_deepswe_release(baseline, treatment, phase="preservation")

    assert report.passed is False
    assert report.losses == ("lost",)
    assert "baseline_solve_regression:lost" in report.failures


def test_promotion_gate_requires_a_flip_and_strictly_more_solved_tasks():
    baseline = _arm(
        [
            _row("kept", True, tokens=120, calls=10, actions=10),
            _row("flip", False, tokens=80, calls=8, actions=8),
        ]
    )
    treatment = _arm(
        [
            _row("kept", True, tokens=90, calls=9, actions=9),
            _row("flip", True, tokens=120, calls=12, actions=12),
        ],
        gt_commit="a" * 40,
    )

    report = assess_deepswe_release(baseline, treatment, phase="promotion")

    assert report.passed is True
    assert report.flips == ("flip",)
    assert report.baseline_solved == 1
    assert report.treatment_solved == 2


def test_gate_rejects_censored_reward_and_manifest_mismatch():
    baseline = _arm([_row("task", True, tokens=100, calls=10, actions=10)])
    treatment = _arm(
        [
            _row(
                "task",
                True,
                tokens=90,
                calls=9,
                actions=9,
                exception={"type": "AgentTimeoutError"},
            )
        ],
        gt_commit="a" * 40,
    )
    treatment["manifest"]["model"] = "different-model"

    report = assess_deepswe_release(baseline, treatment, phase="preservation")

    assert report.passed is False
    assert "manifest_mismatch:model" in report.failures
    assert "censored_treatment:task" in report.failures


def test_gate_rejects_censored_baseline_instead_of_counting_a_false_flip():
    baseline = _arm(
        [
            _row(
                "task",
                False,
                tokens=90,
                calls=9,
                actions=9,
                exception={"type": "AgentTimeoutError"},
            )
        ]
    )
    treatment = _arm(
        [_row("task", True, tokens=80, calls=8, actions=8)],
        gt_commit="a" * 40,
    )

    report = assess_deepswe_release(baseline, treatment, phase="promotion")

    assert report.passed is False
    assert "censored_baseline:task" in report.failures
    assert report.flips == ()


def test_gate_rejects_non_finite_resource_and_setup_values():
    baseline = _arm([_row("task", True, tokens=100, calls=10, actions=10)])
    treatment = _arm(
        [_row("task", True, tokens=80, calls=8, actions=8)],
        gt_commit="a" * 40,
    )
    treatment["rows"][0]["total_tokens"] = math.nan
    treatment["benchmark_setup_overhead"]["input_tokens"] = math.inf

    report = assess_deepswe_release(baseline, treatment, phase="preservation")

    assert report.passed is False
    assert "missing_metric:task:total_tokens" in report.failures
    assert "benchmark_setup_overhead_invalid:input_tokens" in report.failures


def test_gate_rejects_solved_flag_that_disagrees_with_official_verifier_reward():
    baseline = _arm([_row("task", True, tokens=100, calls=10, actions=10)])
    treatment = _arm(
        [_row("task", True, tokens=80, calls=8, actions=8)],
        gt_commit="a" * 40,
    )
    baseline["rows"][0]["rewards"] = {"reward": 0}

    report = assess_deepswe_release(baseline, treatment, phase="preservation")

    assert report.passed is False
    assert "verifier_outcome_invalid:baseline:task" in report.failures


def test_gate_rejects_old_baseline_without_shared_workspace_prompt_contract():
    baseline = _arm([_row("task", True, tokens=100, calls=10, actions=10)])
    treatment = _arm(
        [_row("task", True, tokens=90, calls=9, actions=9)],
        gt_commit="a" * 40,
    )
    baseline["manifest"].pop("workspace_prompt_contract")

    report = assess_deepswe_release(baseline, treatment, phase="preservation")

    assert report.passed is False
    assert "manifest_missing:workspace_prompt_contract" in report.failures


def test_gate_rejects_observed_provider_identity_and_prompt_tool_mismatch():
    baseline = _arm([_row("task", True, tokens=100, calls=10, actions=10)])
    treatment = _arm(
        [_row("task", True, tokens=90, calls=9, actions=9)],
        gt_commit="a" * 40,
    )
    treatment["manifest"]["provider_system_fingerprints"] = ["fp-other"]
    treatment["rows"][0]["tool_schema_sha256"] = "other-tool-schema"
    treatment["rows"][0]["executor_system_fingerprints"] = ["fp-other-row"]

    report = assess_deepswe_release(baseline, treatment, phase="preservation")

    assert report.passed is False
    assert "manifest_mismatch:provider_system_fingerprints" in report.failures
    assert "row_identity_mismatch:task:tool_schema_sha256" in report.failures
    assert "row_identity_mismatch:task:executor_system_fingerprints" in report.failures


def test_gate_rejects_hidden_effective_step_and_wall_time_regressions():
    baseline = _arm([_row("task", True, tokens=100, calls=10, actions=10)])
    treatment = _arm(
        [_row("task", True, tokens=90, calls=9, actions=9)],
        gt_commit="a" * 40,
    )
    treatment["rows"][0]["effective_actions"] = 11
    treatment["rows"][0]["assistant_steps"] = 11
    treatment["rows"][0]["wall_time_sec"] = 11.0

    report = assess_deepswe_release(baseline, treatment, phase="preservation")

    assert report.passed is False
    assert "common_solved_resource_regression:effective_actions" in report.failures
    assert "common_solved_resource_regression:assistant_steps" in report.failures
    assert "common_solved_resource_regression:wall_time_sec" in report.failures


def test_gate_rejects_missing_benchmark_setup_overhead_accounting():
    baseline = _arm([_row("task", True, tokens=100, calls=10, actions=10)])
    treatment = _arm(
        [_row("task", True, tokens=90, calls=9, actions=9)],
        gt_commit="a" * 40,
    )
    treatment.pop("benchmark_setup_overhead")

    report = assess_deepswe_release(baseline, treatment, phase="preservation")

    assert report.passed is False
    assert "benchmark_setup_overhead_missing" in report.failures


def test_gate_rejects_baseline_that_is_not_proven_gt_off():
    baseline = _arm([_row("task", True, tokens=100, calls=10, actions=10)])
    treatment = _arm(
        [_row("task", True, tokens=80, calls=8, actions=8)],
        gt_commit="a" * 40,
    )
    baseline["manifest"].update(
        {
            "arm": "gt_on",
            "integration_mode": "active",
            "comparison_profile": "certified_full",
        }
    )

    report = assess_deepswe_release(baseline, treatment, phase="preservation")

    assert report.passed is False
    assert "baseline_arm_not_off" in report.failures


def test_gate_rejects_zero_setup_usage_cost_or_latency():
    baseline = _arm([_row("task", True, tokens=100, calls=10, actions=10)])
    treatment = _arm(
        [_row("task", True, tokens=80, calls=8, actions=8)],
        gt_commit="a" * 40,
    )
    treatment["benchmark_setup_overhead"].update(
        {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "latency_ms": 0.0}
    )

    report = assess_deepswe_release(baseline, treatment, phase="preservation")

    assert report.passed is False
    for field in ("input_tokens", "output_tokens", "cost_usd", "latency_ms"):
        assert f"benchmark_setup_overhead_nonpositive:{field}" in report.failures


def test_gate_rejects_missing_observed_fingerprints():
    baseline = _arm([_row("task", True, tokens=100, calls=10, actions=10)])
    treatment = _arm(
        [_row("task", True, tokens=80, calls=8, actions=8)],
        gt_commit="a" * 40,
    )
    for arm in (baseline, treatment):
        arm["manifest"]["provider_system_fingerprints"] = []
        arm["rows"][0]["executor_system_fingerprints"] = []

    report = assess_deepswe_release(baseline, treatment, phase="preservation")

    assert report.passed is False
    assert "observed_fingerprint_missing:manifest" in report.failures
    assert "observed_fingerprint_missing:task" in report.failures


def test_setup_overhead_is_included_in_efficiency_verdict():
    baseline = _arm([_row("task", True, tokens=100, calls=10, actions=10)])
    treatment = _arm(
        [_row("task", True, tokens=99, calls=9, actions=9)],
        gt_commit="a" * 40,
    )

    report = assess_deepswe_release(baseline, treatment, phase="preservation")

    assert report.task_resource_deltas["total_tokens"] == -1.0
    assert report.all_in_resource_deltas["total_tokens"] == 14.0
    assert "all_in_resource_regression:total_tokens" in report.failures


def test_gate_fails_closed_on_missing_metric_or_task_mismatch():
    baseline = _arm([_row("task", True, tokens=100, calls=10, actions=10)])
    treatment = _arm(
        [_row("other", True, tokens=90, calls=9, actions=9)],
        gt_commit="a" * 40,
    )
    treatment["rows"][0].pop("decision_actions")

    report = assess_deepswe_release(baseline, treatment, phase="preservation")

    assert report.passed is False
    assert "task_set_mismatch" in report.failures
    assert "missing_metric:other:decision_actions" in report.failures
