from itertools import product

from gt_engine.mechanical_completeness import (
    ProviderBarrierInputsV2,
    build_task_execution_certificate,
    evaluate_provider_barrier,
    evaluate_provider_barrier_v2,
)


def _barrier(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "call": 1,
        "request_payload_sha256": "a" * 64,
        "provider_messages_sha256": "b" * 64,
        "source_snapshot_complete": True,
        "runtime_contract_ready": True,
        "task_semantic_ready": True,
        "graph_applicable": True,
        "graph_current": True,
        "repository_intelligence_ready": True,
        "retrieval_ready": True,
        "persistent_state_ready": True,
        "previous_actions_finalized": True,
        "context_candidate_count": 3,
        "context_accounted_count": 3,
        "contribution_candidate_count": 2,
        "contribution_accounted_count": 2,
        "selected_contribution_ids": ("one", "two"),
        "provider_value_contribution_ids": ("one", "two"),
        "replay_capture_enabled": True,
    }
    values.update(overrides)
    return evaluate_provider_barrier(**values)


def test_provider_barrier_passes_only_with_complete_current_inputs() -> None:
    barrier = _barrier()
    assert barrier["status"] == "PASS"
    assert barrier["failures"] == []
    assert all(row["status"] == "SATISFIED" for row in barrier["requirements"])


def test_v2_provider_barrier_receipts_the_exact_immutable_inputs() -> None:
    inputs = ProviderBarrierInputsV2(
        call=1,
        request_payload_sha256="a" * 64,
        provider_messages_sha256="b" * 64,
        observation_id="observation-1",
        decision_boundary="PRE_EDIT",
        repository_applicability="source_backed",
        graph_required=True,
        graph_input_revision="source-r1",
        graph_revision="graph-r1",
        graph_freshness="CURRENT",
        dense_required=True,
        dense_status="available",
        augmentation_disposition="delivered",
        source_snapshot_complete=True,
        runtime_contract_ready=True,
        task_semantic_ready=True,
        graph_current=True,
        repository_intelligence_ready=True,
        retrieval_ready=True,
        persistent_state_ready=True,
        previous_actions_finalized=True,
        context_candidate_count=1,
        context_accounted_count=1,
        contribution_candidate_count=1,
        contribution_accounted_count=1,
        selected_contribution_ids=("claim-1",),
        provider_value_contribution_ids=("claim-1",),
        replay_capture_enabled=True,
    )

    barrier = evaluate_provider_barrier_v2(inputs)

    assert barrier["schema"] == "gt.provider_mechanical_barrier.v2"
    assert barrier["status"] == "PASS"
    assert barrier["inputs"] == inputs.as_dict()
    assert barrier["inputs_sha256"] == inputs.sha256


def test_provider_barrier_rejects_stale_graph_and_unfinalized_action() -> None:
    barrier = _barrier(graph_current=False, previous_actions_finalized=False)
    assert barrier["status"] == "BLOCKED"
    assert set(barrier["failures"]) == {
        "graph_not_current",
        "previous_action_not_finalized",
    }


def test_provider_barrier_rejects_selected_contribution_without_value_proof() -> None:
    barrier = _barrier(provider_value_contribution_ids=("one",))

    assert barrier["status"] == "BLOCKED"
    assert barrier["failures"] == ["provider_value_certificate_mismatch"]


def test_provider_barrier_blocks_incomplete_mandatory_substrate() -> None:
    barrier = _barrier(
        runtime_contract_ready=False,
        task_semantic_ready=False,
        repository_intelligence_ready=False,
        retrieval_ready=False,
        persistent_state_ready=False,
    )
    assert barrier["status"] == "BLOCKED"
    assert set(barrier["failures"]) == {
        "runtime_contract_missing",
        "task_semantic_substrate_not_ready",
        "repository_intelligence_not_ready",
        "retrieval_not_ready",
        "persistent_state_not_ready",
    }


def test_provider_barrier_records_proven_graph_non_applicability() -> None:
    barrier = _barrier(
        graph_applicable=False,
        graph_current=False,
        repository_intelligence_ready=False,
        retrieval_ready=False,
        persistent_state_ready=False,
    )
    graph = next(
        row for row in barrier["requirements"] if row["requirement_id"] == "graph_current"
    )
    assert graph["status"] == "PROVEN_NOT_APPLICABLE"
    assert sum(
        row["status"] == "PROVEN_NOT_APPLICABLE"
        for row in barrier["requirements"]
    ) == 4
    assert barrier["status"] == "PASS"


def test_terminal_certificate_requires_every_barrier_and_release_check() -> None:
    certificate = build_task_execution_certificate(
        task="fixture",
        provider_barriers=[_barrier()],
        dispatched_calls=1,
        release_checks=[
            {"name": "repository_substrate", "passed": True, "failures": []},
            {"name": "provider_delivery", "passed": True, "failures": []},
        ],
    )
    assert certificate["status"] == "PASS"
    assert certificate["pending_requirement_count"] == 0
    assert certificate["failed_requirement_count"] == 0


def test_terminal_certificate_fails_closed_on_missing_barrier_or_failed_check() -> None:
    certificate = build_task_execution_certificate(
        task="fixture",
        provider_barriers=[_barrier()],
        dispatched_calls=2,
        release_checks=[
            {
                "name": "provider_delivery",
                "passed": False,
                "failures": ["fixture:delivery_missing"],
            }
        ],
    )
    assert certificate["status"] == "BLOCKED"
    assert "provider_barrier_count_mismatch" in certificate["failures"]
    assert "fixture:delivery_missing" in certificate["failures"]


def test_provider_barrier_exhaustive_applicable_truth_table() -> None:
    fields = (
        "runtime_contract_ready",
        "task_semantic_ready",
        "source_snapshot_complete",
        "graph_current",
        "repository_intelligence_ready",
        "retrieval_ready",
        "persistent_state_ready",
        "previous_actions_finalized",
        "replay_capture_enabled",
    )
    for values in product((False, True), repeat=len(fields)):
        barrier = _barrier(**dict(zip(fields, values, strict=True)))
        assert (barrier["status"] == "PASS") is all(values)


def test_provider_barrier_exhaustive_non_applicable_truth_table() -> None:
    mandatory = (
        "runtime_contract_ready",
        "task_semantic_ready",
        "source_snapshot_complete",
        "previous_actions_finalized",
        "replay_capture_enabled",
    )
    graph_only = (
        "graph_current",
        "repository_intelligence_ready",
        "retrieval_ready",
        "persistent_state_ready",
    )
    for mandatory_values in product((False, True), repeat=len(mandatory)):
        for graph_values in product((False, True), repeat=len(graph_only)):
            barrier = _barrier(
                graph_applicable=False,
                **dict(zip(mandatory, mandatory_values, strict=True)),
                **dict(zip(graph_only, graph_values, strict=True)),
            )
            assert (barrier["status"] == "PASS") is all(mandatory_values)


def test_terminal_certificate_is_sensitive_to_each_required_check() -> None:
    check_names = (
        "runtime_identity",
        "repository",
        "retrieval",
        "delivery",
        "actions",
        "persistent_state",
        "replay",
        "artifacts",
    )
    for failed_name in check_names:
        checks = [
            {
                "name": name,
                "passed": name != failed_name,
                "failures": (
                    [] if name != failed_name else [f"fixture:{name}:failed"]
                ),
            }
            for name in check_names
        ]
        certificate = build_task_execution_certificate(
            task="fixture",
            provider_barriers=[_barrier()],
            dispatched_calls=1,
            release_checks=checks,
        )
        assert certificate["status"] == "BLOCKED"
        assert certificate["failed_requirement_count"] == 1
        assert f"fixture:{failed_name}:failed" in certificate["failures"]
