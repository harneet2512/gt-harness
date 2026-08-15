from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from gt_engine.decision_point_eval import (
    DecisionPointValidity,
    validate_decision_point_row,
)
from gt_engine.replay_bundle import ReplayBundleWriter
from scripts.decision_point_eval import audit_bundles


def _row() -> dict:
    control = [
        {"role": "user", "content": "Fix app.py"},
        {"role": "tool", "content": "pytest failed"},
    ]
    payload = "GroundTruth evidence:\n- app.py:12 is called by test_app.py:8"
    treatment = deepcopy(control)
    treatment[1]["content"] += "\n\n" + payload
    return {
        "call": 3,
        "model_name": "test-model",
        "sampling": {"temperature": 1.0},
        "source_revision": "s1",
        "workspace_revision": "w1",
        "provider_tools": [
            {
                "type": "function",
                "function": {"name": "bash", "parameters": {"type": "object"}},
            }
        ],
        "control_provider_messages": control,
        "provider_messages": treatment,
        "response": {
            "role": "assistant",
            "extra": {"actions": [{"command": "sed -n '1,80p' test_app.py"}]},
        },
        "intervention": {
            "payload": payload,
            "message_index": 1,
            "prior_visible_gt_count": 0,
            "selected_contribution_ids": ["gt-contribution-1"],
            "source_revision": "s1",
            "eligible_call": 3,
            "evidence_action": 2,
        },
    }


def test_exact_first_intervention_pair_is_valid():
    result = validate_decision_point_row(_row(), task_id="task-1")

    assert result.validity is DecisionPointValidity.VALID
    assert result.case is not None
    assert result.case.call == 3
    assert result.case.payload.startswith("GroundTruth evidence")


def test_non_gt_byte_difference_is_rejected():
    row = _row()
    row["provider_messages"][0]["content"] = "Different task"

    result = validate_decision_point_row(row, task_id="task-1")

    assert result.validity is DecisionPointValidity.NON_GT_BYTES_DIFFER
    assert result.case is None


def test_prior_visible_gt_context_is_rejected():
    row = _row()
    row["intervention"]["prior_visible_gt_count"] = 1

    result = validate_decision_point_row(row, task_id="task-1")

    assert result.validity is DecisionPointValidity.PRIOR_GT_VISIBLE


def test_stale_or_late_intervention_is_rejected():
    stale = _row()
    stale["intervention"]["source_revision"] = "old"
    late = _row()
    late["intervention"]["eligible_call"] = 2

    assert (
        validate_decision_point_row(stale, task_id="task-1").validity
        is DecisionPointValidity.STALE_EVIDENCE
    )
    assert (
        validate_decision_point_row(late, task_id="task-1").validity
        is DecisionPointValidity.LATE_EVIDENCE
    )


def test_missing_treatment_response_is_rejected():
    row = _row()
    row.pop("response")

    result = validate_decision_point_row(row, task_id="task-1")

    assert result.validity is DecisionPointValidity.MISSING_RESPONSE


def test_missing_tool_schema_is_rejected():
    row = _row()
    row.pop("provider_tools")

    result = validate_decision_point_row(row, task_id="task-1")

    assert result.validity is DecisionPointValidity.MISSING_TOOLS


def test_bundle_audit_counts_only_exact_valid_pairs(tmp_path):
    row = _row()
    path = tmp_path / "task-1__trial" / "agent" / "gt_replay"
    writer = ReplayBundleWriter(path, enabled=True)
    writer.record_request(
        call=row["call"],
        provider_messages=row["provider_messages"],
        control_provider_messages=row["control_provider_messages"],
        intervention=row["intervention"],
        provider_tools=row["provider_tools"],
        request_payload_sha256="request",
        provider_messages_sha256="treatment",
        model_name=row["model_name"],
        model_kwargs={},
        temperature=row["sampling"]["temperature"],
        active_state={},
        source_revision=row["source_revision"],
        workspace_revision=row["workspace_revision"],
    )
    writer.record_response(call=row["call"], response=row["response"])
    writer.finalize()
    unrelated = tmp_path / "task-1__trial" / "artifacts"
    unrelated.mkdir()
    (unrelated / "manifest.json").write_text("{}", encoding="utf-8")

    report = audit_bundles([tmp_path])

    assert report["bundle_count"] == 1
    assert report["valid_case_count"] == 1
    assert report["validity_counts"] == {"valid": 1}


def test_paid_workflow_exposes_bounded_step_limit_for_capture():
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "tb2_miniswe_central.yml"
    ).read_text(encoding="utf-8")

    assert "      step_limit:" in workflow
    assert "STEP_LIMIT: ${{ inputs.step_limit }}" in workflow
    assert '--ak step_limit="$STEP_LIMIT"' in workflow
    assert "--ak step_limit=100" not in workflow
