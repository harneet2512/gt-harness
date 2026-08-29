from __future__ import annotations

from gt_engine.runtime_lifecycle import build_runtime_lifecycle_receipt
from gt_engine.runtime_safety import build_action_accounting


def _accounting():
    return build_action_accounting(
        model_decisions=2,
        tool_actions=1,
        controller_actions=1,
        substrate_probes=2,
        actual_environment_execs=4,
    )


def test_lifecycle_conserves_dispatched_and_not_sent_calls_model_agnostically() -> None:
    receipt = build_runtime_lifecycle_receipt(
        source_revision="source-r1",
        graph_source_revision="graph-source-r1",
        repository_applicability="source_backed",
        repository_substrate_ready=True,
        model_call_contexts=(
            {
                "call": 1,
                "dispatch_status": "response_received",
                "request_payload_sha256": "a" * 64,
                "provider_messages_sha256": "b" * 64,
                "mechanical_completeness_barrier": {
                    "inputs_sha256": "c" * 64,
                },
            },
            {"call": 2, "dispatch_status": "prepared"},
        ),
        action_accounting=_accounting(),
        finalization_complete=True,
    )

    assert receipt.prepared_calls == 2
    assert receipt.dispatched_calls == 1
    assert receipt.not_sent_calls == 1
    assert receipt.lifecycle_conservation_valid is True
    assert receipt.complete is True
    assert receipt.as_dict()["model_agnostic"] is True
    assert receipt.calls[0].provider_barrier_inputs_sha256 == "c" * 64


def test_lifecycle_rejects_unknown_dispatch_state_and_missing_identity() -> None:
    receipt = build_runtime_lifecycle_receipt(
        source_revision="source-r1",
        graph_source_revision="graph-source-r1",
        repository_applicability="source_backed",
        repository_substrate_ready=True,
        model_call_contexts=(
            {"call": 1, "dispatch_status": "response_received"},
            {"call": 2, "dispatch_status": "mystery"},
        ),
        action_accounting=_accounting(),
        finalization_complete=True,
    )

    assert receipt.lifecycle_conservation_valid is False
    assert receipt.complete is False
    assert "unknown_call_dispatch_state" in receipt.reason_codes
    assert "dispatched_call_missing_request_identity" in receipt.reason_codes


def test_lifecycle_keeps_solver_phase_independent_from_failed_optional_substrate() -> None:
    receipt = build_runtime_lifecycle_receipt(
        source_revision="source-r1",
        graph_source_revision="graph-source-r1",
        repository_applicability="source_backed",
        repository_substrate_ready=False,
        model_call_contexts=(
            {
                "call": 1,
                "dispatch_status": "response_received",
                "request_payload_sha256": "a" * 64,
                "provider_messages_sha256": "b" * 64,
            },
        ),
        action_accounting=_accounting(),
        finalization_complete=True,
    )

    phases = {row[0].value: row[1].value for row in receipt.phases}
    assert phases["SUBSTRATE"] == "FAIL"
    assert phases["SOLVER"] == "PASS"
    assert receipt.complete is True
