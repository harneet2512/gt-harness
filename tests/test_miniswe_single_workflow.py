import pytest

from scripts.miniswe_gt_run import (
    GTOffControlError,
    validate_gt_off_control,
)


def test_historical_single_witness_version_is_not_a_supported_scaffold():
    from eval.miniswe_agent import _ALLOWED_MINISWE_AGENT_VERSIONS

    assert _ALLOWED_MINISWE_AGENT_VERSIONS == {"2.4.6"}


def _control_identity() -> dict[str, object]:
    return {
        "model_label": "deepseek 0731 v4",
        "served_model": "deepseek-v4-flash",
        "miniswe_agent_version": "2.4.6",
        "task_set_hash": "tasks-r1",
        "source_revision": "source-r1",
        "scaffold_hash": "scaffold-r1",
        "provider_config_hash": "provider-config-r1",
        "temperature": 1.0,
        "step_limit": 100,
        "timeout": 30,
        "environment_hash": "environment-r1",
    }


def test_gt_off_control_receipt_is_deterministic_and_identity_bound() -> None:
    events = (
        {"event": "agent.start", "task": "task-1"},
        {"event": "model.request", "sequence": 1},
        {"event": "agent.finish", "exit_code": 0},
    )
    first = validate_gt_off_control(identity=_control_identity(), events=events)
    second = validate_gt_off_control(
        identity=dict(_control_identity()), events=tuple(reversed(tuple(reversed(events))))
    )

    assert first == second
    assert first["schema"] == "gt.off_control_receipt.v1"
    assert first["gt_enabled"] is False
    assert first["model_label"] == "deepseek 0731 v4"
    assert first["trace_event_count"] == 3
    assert first["research_valid"] is True


def test_gt_off_control_rejects_gt_hook_or_identity_mutation() -> None:
    with pytest.raises(GTOffControlError, match="gt_hook_in_trace"):
        validate_gt_off_control(
            identity=_control_identity(),
            events=({"event": "gt_engine.delivery", "sequence": 1},),
        )

    changed = _control_identity()
    changed["source_revision"] = "source-mutated"
    with pytest.raises(GTOffControlError, match="identity_mismatch"):
        validate_gt_off_control(
            identity=changed,
            expected_identity=_control_identity(),
            events=(),
        )
