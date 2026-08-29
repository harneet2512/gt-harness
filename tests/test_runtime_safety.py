from gt_engine.runtime_safety import (
    ActionActor,
    MetricState,
    TreatmentValidity,
    assess_provider_dispatch,
    build_action_accounting,
    measure_rate,
    measure_ratio,
)


def test_mechanical_failure_invalidates_treatment_without_blocking_dispatch() -> None:
    assessment = assess_provider_dispatch(
        {
            "schema": "gt.provider_mechanical_barrier.v1",
            "call": 1,
            "status": "BLOCKED",
            "failures": ["graph_not_current", "replay_capture_disabled"],
        }
    )

    assert assessment.dispatch_allowed is True
    assert assessment.treatment_validity is TreatmentValidity.INVALID
    assert assessment.reason_codes == (
        "graph_not_current",
        "replay_capture_disabled",
    )
    assert assessment.as_dict() == {
        "schema": "gt.provider_dispatch_assessment.v1",
        "dispatch_allowed": True,
        "treatment_validity": "INVALID",
        "reason_codes": ["graph_not_current", "replay_capture_disabled"],
    }


def test_complete_mechanical_evidence_keeps_treatment_valid() -> None:
    assessment = assess_provider_dispatch(
        {
            "schema": "gt.provider_mechanical_barrier.v1",
            "call": 1,
            "status": "PASS",
            "failures": [],
        }
    )

    assert assessment.dispatch_allowed is True
    assert assessment.treatment_validity is TreatmentValidity.VALID
    assert assessment.reason_codes == ()


def test_benchmark_mechanical_failure_blocks_provider_dispatch() -> None:
    assessment = assess_provider_dispatch(
        {
            "schema": "gt.provider_mechanical_barrier.v2",
            "call": 1,
            "status": "BLOCKED",
            "failures": ["graph_not_current"],
        },
        fail_closed=True,
    )

    assert assessment.dispatch_allowed is False
    assert assessment.treatment_validity is TreatmentValidity.INVALID
    assert assessment.reason_codes == ("graph_not_current",)


def test_zero_denominator_is_not_measured_instead_of_perfect() -> None:
    measurement = measure_rate(0, 0)

    assert measurement.state is MetricState.NOT_MEASURED
    assert measurement.value is None
    assert measurement.as_dict() == {
        "schema": "gt.rate_measurement.v1",
        "state": "NOT_MEASURED",
        "numerator": 0,
        "denominator": 0,
        "value": None,
        "reason_codes": ["zero_denominator"],
    }


def test_zero_denominator_ratio_is_not_fabricated_as_zero() -> None:
    measurement = measure_ratio(0, 0)

    assert measurement.state is MetricState.NOT_MEASURED
    assert measurement.value is None
    assert measurement.as_dict()["schema"] == "gt.ratio_measurement.v1"


def test_rate_measurement_rejects_impossible_counts() -> None:
    measurement = measure_rate(2, 1)

    assert measurement.state is MetricState.INVALID
    assert measurement.value is None
    assert measurement.reason_codes == ("numerator_exceeds_denominator",)


def test_action_accounting_separates_model_tool_controller_and_substrate_work() -> None:
    accounting = build_action_accounting(
        model_decisions=2,
        tool_actions=1,
        controller_actions=3,
        substrate_probes=5,
        actual_environment_execs=10,
    )

    assert accounting.conservation_valid is True
    assert accounting.counts == {
        ActionActor.MODEL_DECISION: 2,
        ActionActor.TOOL_ACTION: 1,
        ActionActor.CONTROLLER_ACTION: 3,
        ActionActor.SUBSTRATE_PROBE: 5,
        ActionActor.HOST_OTHER: 1,
    }
    assert accounting.as_dict()["host_execution_total"] == 10
