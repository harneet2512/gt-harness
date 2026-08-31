from pathlib import Path

import pytest

from scripts.miniswe_gt_run import (
    GTOffControlError,
    validate_gt_off_control,
)

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "tb2_miniswe_gt_single.yml"
BASELINE_FINGERPRINT = "fp_a18b46594c_prod0820_fp8_kvcache_20260402"
TASK_CHECKSUM = "13c4e35adbd7e55707f273aabd8f4108672f0fb790c96af543fbcbdcc977b119"


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_single_witness_workflow_is_closed_and_exactly_one_trial():
    text = _workflow_text()

    assert "workflow_dispatch:" in text
    assert "${{ inputs." not in text
    assert 'MODEL: "deepseek-v4-flash"' in text
    assert 'TASK_ID: "fix-code-vulnerability"' in text
    assert 'MINISWE_AGENT_VERSION: "2.2.8"' in text
    assert "-a eval.miniswe_agent:MiniSweGtAgent" in text
    assert '-i "$TASK_ID"' in text
    assert '-n "1"' in text
    assert '-k "1"' in text
    assert '--agent-timeout-multiplier "1.0"' in text
    assert "-l " not in text


def test_single_witness_workflow_gates_snapshot_task_and_runner_defaults():
    text = _workflow_text()

    assert BASELINE_FINGERPRINT in text
    assert TASK_CHECKSUM in text
    assert 'BASELINE_SERVED_MODEL: "deepseek-v4-flash"' in text
    assert "served_model != expected_model" in text
    assert "fingerprint != expected_fingerprint" in text
    assert 'result.get("task_checksum") != expected' in text
    assert 'parameters["step_limit"].default == 100' in text
    assert 'model_fields["cost_limit"].default == 3.0' in text


def test_single_witness_workflow_builds_gt_and_preserves_receipts_on_failure():
    text = _workflow_text()

    assert "go build -tags sqlite_fts5" in text
    assert 'GT_INDEX_BINARY_HOST: ${{ github.workspace }}/vendor/gt-index-linux-amd64' in text
    assert "if: always()" in text
    assert "actions/upload-artifact@v4" in text
    assert "results/terminal-bench/" in text


def test_single_witness_compiles_pair_and_binds_execution_identity():
    text = _workflow_text()

    assert "scripts/phase2_single_witness.py analyze" in text
    assert "fs024_single_witness_baseline.json" in text
    assert '"provider_trial_count": 1' in text
    assert 'os.environ["GITHUB_RUN_ID"]' in text
    assert "single_witness_analysis.json" in text
    assert "witness-receipts/execution.json" in text


def test_single_witness_workflow_requires_verifier_trajectory_and_gt_receipts():
    text = _workflow_text()

    assert 'result.get("verifier_result")' in text
    assert 'trial_dir / "agent" / "miniswe_trajectory.json"' in text
    assert 'trial_dir / "agent" / "miniswe_report.json"' in text
    assert 'gt_state.rglob("events.jsonl")' in text
    assert 'gt_state.rglob("provider_events.jsonl")' in text
    assert 'gt_state.rglob("reproducibility_manifest.json")' in text


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
