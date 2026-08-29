from gt_engine.central_runtime import CENTRAL_FEATURE_IDS


def test_registry_covers_every_active_gt_subsystem_and_all_18_features():
    from gt_engine.component_registry import (
        ACTIVE_GT_COMPONENTS,
        FEATURE_COMPONENT_CONTRACTS,
    )

    assert set(FEATURE_COMPONENT_CONTRACTS) == set(CENTRAL_FEATURE_IDS)
    assert len(FEATURE_COMPONENT_CONTRACTS) == 18
    assert {
        "workspace_sensor",
        "repository_graph",
        "hybrid_retrieval",
        "preflight",
        "postflight_features",
        "contribution_compiler",
        "provider_delivery",
        "validation_classifier",
        "completion_controller",
        "progress_controller",
        "context_compactor",
        "replay_capture",
        "persistent_execution_state",
        "repository_context_engine",
    } <= set(ACTIVE_GT_COMPONENTS)


def test_only_evidence_correct_features_are_postflight_only():
    from gt_engine.component_registry import FEATURE_COMPONENT_CONTRACTS

    actual = {
        feature_id
        for feature_id, contract in FEATURE_COMPONENT_CONTRACTS.items()
        if contract.postflight_only
    }
    assert actual == {
        "GT_CHANGE_SURFACE",
        "signature_delta",
        "GT_PATCH_DELTA",
        "syntax_result",
        "covering_red",
    }


def test_component_registry_is_machine_auditable_and_fail_closed():
    from gt_engine.component_registry import audit_component_registry

    result = audit_component_registry()
    assert result["ready"] is True
    assert result["feature_count"] == 18
    assert result["duplicate_components"] == []
    assert result["missing_feature_placements"] == []
    assert result["invalid_delivery_contracts"] == []
