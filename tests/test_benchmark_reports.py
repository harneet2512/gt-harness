from gt_engine.benchmark_reports import build_benchmark_reports


def test_reports_keep_integrity_solve_efficiency_and_intervention_separate():
    baseline = {
        "rows": [
            {"task": "a", "solved": True},
            {"task": "b", "solved": False},
            {"task": "c", "solved": True},
            {"task": "d", "solved": False},
        ]
    }
    treatment = {
        "rows": [
            {"task": "a", "solved": True},
            {"task": "b", "solved": True},
            {"task": "c", "solved": False},
            {"task": "d", "solved": False},
        ]
    }
    metrics = [
        {
            "task": "a",
            "api_calls": 2,
            "actions": 3,
            "preemptive_retrieval_shared_computations": 1,
            "provider_delivery_count": 2,
            "provider_delivery_visible_chars": 100,
            "intervention_chain_rows": 2,
            "intervention_surface_counts": {"repository_context": 2},
            "behavioral_uptake": {"VALIDATION_ACTION": 2},
        },
        {"task": "b"},
        {"task": "c"},
        {"task": "d"},
    ]

    reports = build_benchmark_reports(
        expected_tasks=("a", "b", "c", "d"),
        baseline=baseline,
        treatment=treatment,
        receipt_metrics=metrics,
        integrity_failures=(),
        efficiency={"common_solved_resource_deltas": {"provider_calls": -1}},
    )

    assert reports["integrity"]["passed"] is True
    assert reports["solve"]["categories"] == {
        "both_solve": ["a"],
        "baseline_only": ["c"],
        "gt_only": ["b"],
        "both_fail": ["d"],
    }
    assert reports["solve"]["causal_claim_policy"] == "counterfactual_required"
    assert reports["efficiency"]["exact_operations"][0]["retrieval_computations"] == 1
    assert reports["efficiency"]["valid"] is True
    assert reports["intervention"]["surface_counts"] == {"repository_context": 2}
    assert reports["intervention"]["causal_status"] == (
        "UNIDENTIFIABLE_WITHOUT_COUNTERFACTUAL"
    )


def test_integrity_report_fails_for_missing_or_duplicate_task_artifacts():
    reports = build_benchmark_reports(
        expected_tasks=("a", "b"),
        baseline={"rows": []},
        treatment={"rows": [{"task": "a", "solved": False}]},
        receipt_metrics=[{"task": "a"}],
        integrity_failures=("b:receipt_artifact_count:0",),
        efficiency={},
    )

    assert reports["integrity"]["passed"] is False
    assert reports["integrity"]["complete_task_set"] is False
    assert reports["efficiency"]["valid"] is False
    assert reports["efficiency"]["aggregate"] is None
