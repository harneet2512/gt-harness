from __future__ import annotations

import json

from scripts.gt_live_gate import evaluate_live_gate


def _task(
    name,
    features,
    lifecycle=None,
    *,
    provider_temperatures=None,
    expected_profile_controls=None,
    active_profile_controls=None,
    missing_profile_controls=None,
    profile_behavior_flags=None,
    profile_receipt_fault="",
    task_role="",
    obligation_count=0,
    shipped_obligation_count=0,
    verify_obligation_total=0,
    graph_surface_receipt_present=False,
    graph_projection_present=False,
    graph_available=False,
    verification_plan_evaluated=False,
    role_pack_present=False,
    role_pack_id="",
    role_pack_version="",
    predicate_compiled_count=0,
    tool_results=0,
    tool_outcome_classified_count=0,
    tool_outcome_counts=None,
    graph_refresh_failure_count=0,
    graph_refresh_recovered_count=0,
    capsule_repeated_exposure_count=0,
    utility_scored_count=0,
    utility_selected_count=0,
    graph_projection_revision="",
    graph_router_revision="",
    predicate_invalid_receipt_count=0,
    graph_evidence_unlinked_count=0,
    graph_evidence_revision_mismatch_count=0,
    shell_lifecycle_unrecovered_count=0,
    task_start_localization_provider_iteration=0,
    task_start_localization_response_iteration=0,
    task_start_localization_compound=False,
    task_start_localization_eligible=False,
):
    return {
        "task_name": name,
        "agent_error": None,
        "exception_info": None,
        "attribution_issues": [],
        "ledger_issues": [],
        "dose_violations": [],
        "feature_attribution": features,
        "lifecycle_checkpoints": lifecycle or {},
        "provider_temperatures": provider_temperatures or [],
        "expected_profile_controls": expected_profile_controls or [],
        "active_profile_controls": active_profile_controls or [],
        "missing_profile_controls": missing_profile_controls or [],
        "profile_behavior_flags": profile_behavior_flags or [],
        "profile_receipt_fault": profile_receipt_fault,
        "task_role": task_role,
        "obligation_count": obligation_count,
        "shipped_obligation_count": shipped_obligation_count,
        "verify_obligation_total": verify_obligation_total,
        "graph_surface_receipt_present": graph_surface_receipt_present,
        "graph_projection_present": graph_projection_present,
        "graph_available": graph_available,
        "verification_plan_evaluated": verification_plan_evaluated,
        "role_pack_present": role_pack_present,
        "role_pack_id": role_pack_id,
        "role_pack_version": role_pack_version,
        "predicate_compiled_count": predicate_compiled_count,
        "tool_results": tool_results,
        "tool_outcome_classified_count": tool_outcome_classified_count,
        "tool_outcome_counts": tool_outcome_counts or {},
        "graph_refresh_failure_count": graph_refresh_failure_count,
        "graph_refresh_recovered_count": graph_refresh_recovered_count,
        "capsule_repeated_exposure_count": (
            capsule_repeated_exposure_count
        ),
        "utility_scored_count": utility_scored_count,
        "utility_selected_count": utility_selected_count,
        "graph_projection_revision": graph_projection_revision,
        "graph_router_revision": graph_router_revision,
        "predicate_invalid_receipt_count": (
            predicate_invalid_receipt_count
        ),
        "graph_evidence_unlinked_count": graph_evidence_unlinked_count,
        "graph_evidence_revision_mismatch_count": (
            graph_evidence_revision_mismatch_count
        ),
        "shell_lifecycle_unrecovered_count": (
            shell_lifecycle_unrecovered_count
        ),
        "task_start_localization_provider_iteration": (
            task_start_localization_provider_iteration
        ),
        "task_start_localization_response_iteration": (
            task_start_localization_response_iteration
        ),
        "task_start_localization_compound": (
            task_start_localization_compound
        ),
        "task_start_localization_eligible": (
            task_start_localization_eligible
        ),
    }


def _feature(status="WITNESSED", *, delivered=True, exposed=True):
    return {
        "status": status,
        "deliveries": ["d1"] if delivered else [],
        "exposed": exposed,
        "action_consistent": status == "WITNESSED",
    }


def test_live_gate_accepts_healthy_provider_bound_feature_union(tmp_path):
    trial = tmp_path / "task__trial"
    trial.mkdir()
    (trial / "result.json").write_text(json.dumps({
        "config": {"agent": {"model_name": "deepseek-v4-flash"}},
    }), encoding="utf-8")
    audit = {
        "tasks": [
            _task("task", {
                "obligations": _feature(),
                "localization": _feature(),
                "GT_LOC_RESLOT": _feature(),
            }),
        ],
    }

    report = evaluate_live_gate(
        audit,
        min_witnessed=3,
        expected_tasks=1,
        expected_model="deepseek-v4-flash",
        run_dir=tmp_path,
    )

    assert report["passed"] is True
    assert report["witnessed_count"] == 3


def test_live_gate_requires_compound_localization_on_first_provider_request(
        tmp_path):
    trial = tmp_path / "task__trial"
    trial.mkdir()
    (trial / "result.json").write_text(json.dumps({
        "config": {"agent": {"model_name": "deepseek-v4-flash"}},
    }), encoding="utf-8")
    audit = {
        "tasks": [
            _task(
                "on-time",
                {"localization": _feature()},
                task_start_localization_provider_iteration=1,
                task_start_localization_response_iteration=1,
                task_start_localization_compound=True,
                task_start_localization_eligible=True,
            ),
            _task(
                "late",
                {"localization": _feature()},
                task_start_localization_provider_iteration=3,
                task_start_localization_response_iteration=3,
                task_start_localization_compound=True,
                task_start_localization_eligible=True,
            ),
        ],
    }

    report = evaluate_live_gate(
        audit,
        min_witnessed=1,
        expected_tasks=2,
        expected_model="deepseek-v4-flash",
        require_step0_localization=True,
        run_dir=tmp_path,
    )

    assert report["passed"] is False
    assert any(
        "late: task-start localization reached provider iteration 3" in issue
        for issue in report["issues"]
    )


def test_live_gate_rejects_dark_unexposed_and_wrong_model(tmp_path):
    trial = tmp_path / "task__trial"
    trial.mkdir()
    (trial / "result.json").write_text(json.dumps({
        "config": {"agent": {"model_name": "other-model"}},
    }), encoding="utf-8")
    audit = {
        "tasks": [
            _task("task", {
                "localization": _feature(
                    "TRIGGERED_DARK", delivered=False, exposed=False
                ),
                "caller_contract": _feature(exposed=False),
            }),
        ],
    }

    report = evaluate_live_gate(
        audit,
        min_witnessed=2,
        expected_tasks=1,
        expected_model="deepseek-v4-flash",
        run_dir=tmp_path,
    )

    assert report["passed"] is False
    assert any("went dark" in issue for issue in report["issues"])
    assert any("unexposed" in issue for issue in report["issues"])
    assert any("expected model" in issue for issue in report["issues"])


def test_live_gate_requires_sdlc_checkpoint_union(tmp_path):
    trial = tmp_path / "task__trial"
    trial.mkdir()
    (trial / "result.json").write_text(json.dumps({
        "config": {"agent": {"model_name": "deepseek-v4-flash"}},
    }), encoding="utf-8")
    audit = {
        "tasks": [
            _task(
                "task",
                {"obligations": _feature()},
                lifecycle={
                    "task_start": {"count": 1},
                    "research": {"count": 2},
                    "pre_edit": {"count": 1},
                    "post_edit": {"count": 1},
                    "verify": {"count": 1},
                    "submit": {"count": 1},
                },
            ),
        ],
    }

    report = evaluate_live_gate(
        audit,
        min_witnessed=1,
        expected_tasks=1,
        expected_model="deepseek-v4-flash",
        required_lifecycle=(
            "task_start", "research", "pre_edit", "post_edit",
            "test", "verify", "submit",
        ),
        run_dir=tmp_path,
    )

    assert report["passed"] is False
    assert report["missing_lifecycle"] == ["test"]
    assert any("missing SDLC" in issue for issue in report["issues"])


def test_live_gate_requires_contract_graph_and_graph_edit_plan(tmp_path):
    trial = tmp_path / "task__trial"
    trial.mkdir()
    (trial / "result.json").write_text(json.dumps({
        "config": {"agent": {"model_name": "deepseek-v4-flash"}},
    }), encoding="utf-8")
    healthy = _task(
        "healthy",
        {"obligations": _feature()},
        lifecycle={"post_edit": {"count": 1}, "verify": {"count": 1}},
        task_role="code_behavior",
        obligation_count=3,
        shipped_obligation_count=3,
        verify_obligation_total=3,
        graph_surface_receipt_present=True,
        graph_projection_present=True,
        graph_available=True,
        verification_plan_evaluated=True,
    )
    report = evaluate_live_gate(
        {"tasks": [healthy]},
        min_witnessed=1,
        expected_tasks=1,
        expected_model="deepseek-v4-flash",
        require_task_contract=True,
        require_graph_surface_receipt=True,
        require_verification_plan_on_graph_edit=True,
        run_dir=tmp_path,
    )
    assert report["passed"] is True

    broken = dict(healthy)
    broken.update({
        "shipped_obligation_count": 2,
        "graph_projection_present": False,
        "verification_plan_evaluated": False,
    })
    report = evaluate_live_gate(
        {"tasks": [broken]},
        min_witnessed=1,
        expected_tasks=1,
        expected_model="deepseek-v4-flash",
        require_task_contract=True,
        require_graph_surface_receipt=True,
        require_verification_plan_on_graph_edit=True,
        run_dir=tmp_path,
    )
    assert report["passed"] is False
    assert any("incomplete task contract" in item for item in report["issues"])
    assert any("missing graph surface" in item for item in report["issues"])
    assert any("did not evaluate" in item for item in report["issues"])


def test_live_gate_requires_complete_improvement_receipts(tmp_path):
    trial = tmp_path / "task__trial"
    trial.mkdir()
    (trial / "result.json").write_text(json.dumps({
        "config": {"agent": {"model_name": "deepseek-v4-flash"}},
    }), encoding="utf-8")
    healthy = _task(
        "healthy",
        {"obligations": _feature()},
        obligation_count=2,
        role_pack_present=True,
        role_pack_id="code-build",
        role_pack_version="1",
        predicate_compiled_count=2,
        tool_results=3,
        tool_outcome_classified_count=3,
        tool_outcome_counts={"success": 2, "useful_red": 1},
        utility_scored_count=2,
        utility_selected_count=1,
        graph_available=True,
        graph_projection_revision="graph-r1",
        graph_router_revision="graph-r1",
    )
    report = evaluate_live_gate(
        {"tasks": [healthy]},
        min_witnessed=1,
        expected_tasks=1,
        expected_model="deepseek-v4-flash",
        require_improvement_receipts=True,
        run_dir=tmp_path,
    )
    assert report["passed"] is True

    broken = dict(healthy)
    broken.update({
        "role_pack_present": False,
        "predicate_compiled_count": 1,
        "tool_outcome_classified_count": 2,
        "tool_outcome_counts": {"unknown": 1, "shell_lifecycle": 1},
        "graph_refresh_failure_count": 1,
        "capsule_repeated_exposure_count": 1,
        "utility_selected_count": 3,
        "graph_router_revision": "graph-r0",
        "predicate_invalid_receipt_count": 1,
        "graph_evidence_unlinked_count": 1,
        "graph_evidence_revision_mismatch_count": 1,
        "shell_lifecycle_unrecovered_count": 1,
    })
    report = evaluate_live_gate(
        {"tasks": [broken]},
        min_witnessed=1,
        expected_tasks=1,
        expected_model="deepseek-v4-flash",
        require_improvement_receipts=True,
        run_dir=tmp_path,
    )
    assert report["passed"] is False
    joined = "\n".join(report["issues"])
    assert "missing role-pack" in joined
    assert "predicate compilation mismatch" in joined
    assert "invalid semantic predicate receipt" in joined
    assert "tool-outcome census mismatch" in joined
    assert "unknown tool outcome" in joined
    assert "unrecovered persistent shell" in joined
    assert "graph context refresh failure" in joined
    assert "capsule repeated" in joined
    assert "invalid utility selection" in joined
    assert "projection/router revision mismatch" in joined
    assert "decision-irrelevant graph evidence" in joined
    assert "stale graph evidence revision" in joined


def test_live_gate_requires_complete_census_temperature_and_actions(tmp_path):
    from gt_engine.attribution import DIRECT_FEATURES

    trial = tmp_path / "task__trial"
    trial.mkdir()
    (trial / "result.json").write_text(json.dumps({
        "config": {"agent": {"model_name": "deepseek-v4-flash"}},
    }), encoding="utf-8")
    features = {
        feature_id: _feature(
            "INELIGIBLE", delivered=False, exposed=False
        )
        for feature_id in DIRECT_FEATURES
    }
    features["obligations"] = _feature()
    features["localization"] = _feature()
    audit = {
        "tasks": [
            _task(
                "task",
                features,
                provider_temperatures=[1.0],
            ),
        ],
    }

    report = evaluate_live_gate(
        audit,
        min_witnessed=2,
        min_action_consistent=2,
        expected_tasks=1,
        expected_model="deepseek-v4-flash",
        expected_temperature=1.0,
        require_complete_census=True,
        run_dir=tmp_path,
    )

    assert report["passed"] is True
    assert report["provider_temperatures"] == [1.0]
    assert report["complete_census"] is True


def test_live_gate_rejects_missing_identity_wrong_temperature_and_too_few_actions(
    tmp_path,
):
    trial = tmp_path / "task__trial"
    trial.mkdir()
    (trial / "result.json").write_text(json.dumps({
        "config": {"agent": {"model_name": "deepseek-v4-flash"}},
    }), encoding="utf-8")
    audit = {
        "tasks": [
            _task(
                "task",
                {"obligations": _feature()},
                provider_temperatures=[0.7],
            ),
        ],
    }

    report = evaluate_live_gate(
        audit,
        min_witnessed=1,
        min_action_consistent=2,
        expected_tasks=1,
        expected_model="deepseek-v4-flash",
        expected_temperature=1.0,
        require_complete_census=True,
        run_dir=tmp_path,
    )

    assert report["passed"] is False
    assert any("feature census" in issue for issue in report["issues"])
    assert any("temperature" in issue for issue in report["issues"])
    assert any("action-consistent" in issue for issue in report["issues"])


def test_live_gate_requires_profile_controls_and_behavior_flags(tmp_path):
    trial = tmp_path / "task__trial"
    trial.mkdir()
    (trial / "result.json").write_text(json.dumps({
        "config": {"agent": {"model_name": "deepseek-v4-flash"}},
    }), encoding="utf-8")
    audit = {
        "tasks": [
            _task(
                "task",
                {"obligations": _feature()},
                missing_profile_controls=["GT_CS_EDIT_TRIGGER"],
                profile_behavior_flags=[],
            ),
        ],
    }

    report = evaluate_live_gate(
        audit,
        min_witnessed=1,
        expected_tasks=1,
        expected_model="deepseek-v4-flash",
        required_behavior_flags=("GT_CS_EDIT_TRIGGER",),
        require_complete_profile=True,
        run_dir=tmp_path,
    )

    assert report["passed"] is False
    assert any("profile control" in issue for issue in report["issues"])
    assert any("behavior flag" in issue for issue in report["issues"])


def test_live_gate_accepts_complete_profile_and_required_behavior(tmp_path):
    trial = tmp_path / "task__trial"
    trial.mkdir()
    (trial / "result.json").write_text(json.dumps({
        "config": {"agent": {"model_name": "deepseek-v4-flash"}},
    }), encoding="utf-8")
    controls = ["GT_GATEWAY", "GT_CS_EDIT_TRIGGER"]
    audit = {
        "tasks": [
            _task(
                "task",
                {"obligations": _feature()},
                expected_profile_controls=controls,
                active_profile_controls=controls,
                profile_behavior_flags=["GT_CS_EDIT_TRIGGER"],
            ),
        ],
    }

    report = evaluate_live_gate(
        audit,
        min_witnessed=1,
        expected_tasks=1,
        expected_model="deepseek-v4-flash",
        required_behavior_flags=("GT_CS_EDIT_TRIGGER",),
        require_complete_profile=True,
        run_dir=tmp_path,
    )

    assert report["passed"] is True
    assert report["complete_profile"] is True
    assert report["observed_behavior_flags"] == ["GT_CS_EDIT_TRIGGER"]


def test_live_gate_requires_exercised_not_merely_censused_features(tmp_path):
    trial = tmp_path / "task__trial"
    trial.mkdir()
    (trial / "result.json").write_text(json.dumps({
        "config": {"agent": {"model_name": "deepseek-v4-flash"}},
    }), encoding="utf-8")
    audit = {
        "tasks": [
            _task(
                "task",
                {
                    "obligations": _feature(),
                    "recovery": {
                        **_feature(
                            "INELIGIBLE", delivered=False, exposed=False
                        ),
                        "reasons": ["trigger_not_satisfied"],
                    },
                    "def_partition": {
                        **_feature(
                            "INELIGIBLE", delivered=False, exposed=False
                        ),
                        "reasons": ["no_trigger_observed"],
                    },
                },
            ),
        ],
    }

    report = evaluate_live_gate(
        audit,
        min_witnessed=1,
        min_exercised=3,
        expected_tasks=1,
        expected_model="deepseek-v4-flash",
        run_dir=tmp_path,
    )

    assert report["passed"] is False
    assert report["exercised_features"] == ["obligations", "recovery"]
    assert any("exercised identities 2 < required 3" in issue
               for issue in report["issues"])


def test_recovered_graph_refresh_is_not_reported_as_unhandled_fault(tmp_path):
    trial = tmp_path / "task__trial"
    trial.mkdir()
    (trial / "result.json").write_text(json.dumps({
        "config": {"agent": {"model_name": "deepseek-v4-flash"}},
    }), encoding="utf-8")
    audit = {"tasks": [_task(
        "task",
        {"obligations": _feature()},
        graph_refresh_failure_count=1,
        graph_refresh_recovered_count=1,
    )]}
    report = evaluate_live_gate(
        audit,
        min_witnessed=1,
        expected_tasks=1,
        expected_model="deepseek-v4-flash",
        require_improvement_receipts=True,
        run_dir=tmp_path,
    )
    assert not any("graph context refresh failure" in issue
                   for issue in report["issues"])
