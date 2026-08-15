"""Tests for the provider-free trajectory audit boundary."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.central_trajectory_audit import audit_run_root


def test_direct_trajectory_audit_invocation_bootstraps_project_imports():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "scripts" / "central_trajectory_audit.py"), "--help"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Fail-closed trajectory audit" in result.stdout


def _write_bundle(
    root: Path,
    *,
    complete_hashes: bool = True,
    task_directory: str = "trial-task-demo",
) -> None:
    task = root / task_directory / "agent" if task_directory else root / "agent"
    task.mkdir(parents=True)
    trajectory = {
        "info": {"exit_status": "Submitted"},
        "messages": [
            {"role": "user", "content": "Fix it."},
            {
                "role": "assistant",
                "content": "",
                "reasoning_content": "inspect",
                "extra": {"actions": [{"command": "cat app.py", "tool_call_id": "call-1"}]},
            },
            {
                "role": "tool",
                "tool_call_id": "call-1",
                "content": "ok",
                "extra": {"returncode": 0, "raw_output": "print(1)"},
            },
        ],
    }
    (task / "miniswe_trajectory.json").write_text(json.dumps(trajectory), encoding="utf-8")
    request_hash = "h" if complete_hashes else ""
    receipt = {
        "actions": 1,
        "model_call_contexts": [
            {
                "call": 1,
                "request_payload_sha256": request_hash,
                "provider_messages_sha256": request_hash,
                "provider_message_count": 3,
                "provider_changed_message_indices": [2],
                "dispatch_status": "response_received",
                "context_fact_candidates": 1,
                "context_facts_represented": 1,
                "context_facts_selected": 0,
                "context_facts_controller_only": 0,
                "context_facts_omitted": 0,
                "context_facts_accounted": 1,
            }
        ],
        "features": {
            "effect_trace": [
                {
                    "effect_id": "receipt-1",
                    "feature_id": "GT_LOC_RESLOT",
                    "evidence_action": 1,
                    "applied_call": 1,
                    "source_revision": "src-1",
                    "disposition": "provider_payload",
                    "provider_delivery_ids": ["delivery-1"],
                    "timing": {"late": False, "predictive": False},
                }
            ]
        },
        "guidance_deliveries": [
            {
                "feature_id": "GT_LOC_RESLOT",
                "evidence_action": 1,
                "first_eligible_call": 1,
                "delivered_before_call": 1,
                "delivered_before_model_query": True,
                "one_step_late": False,
                "not_predictive": True,
                "request_payload_sha256": request_hash,
                "provider_messages_sha256": request_hash,
                "message_index": 2,
                "facts": [{"path": "app.py", "line": 1, "symbol": "x"}],
                "chars": 20,
            }
        ],
    }
    (task / "central_receipt.json").write_text(json.dumps(receipt), encoding="utf-8")


def test_audit_certifies_deterministic_integrity_but_not_model_causality(tmp_path):
    _write_bundle(tmp_path)

    report = audit_run_root(tmp_path)

    assert report["audit_status"] == "DETERMINISTIC_AUDIT_CERTIFIED"
    assert report["certification"]["model_causality"] == "UNIDENTIFIABLE"
    assert report["certification"]["replay_state_available"] is False
    delivery = report["tasks"]["demo"]["deliveries"][0]
    assert delivery["deterministic_status"] == "VALID"
    assert delivery["causal_status"] == "UNIDENTIFIABLE_NO_REPLAY_STATE"


def test_audit_fails_closed_on_missing_provider_hash(tmp_path):
    _write_bundle(tmp_path, complete_hashes=False)

    report = audit_run_root(tmp_path)

    assert report["audit_status"] == "DETERMINISTIC_AUDIT_FAILED"
    assert any("provider_request_hash" in item for item in report["failures"])


def test_audit_fails_closed_on_missing_context_accounting(tmp_path):
    _write_bundle(tmp_path)
    receipt_path = next(tmp_path.rglob("central_receipt.json"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["model_call_contexts"][0].pop("context_fact_candidates")
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    report = audit_run_root(tmp_path)

    assert report["audit_status"] == "DETERMINISTIC_AUDIT_FAILED"
    assert any("missing_context_fact_accounting" in item for item in report["failures"])


def test_audit_discovers_deepswe_trial_layout_by_task_name(tmp_path):
    for task_name in ("alpha-fix", "beta-fix"):
        trial = (
            tmp_path
            / f"deepswe-central-123-{task_name}"
            / "results"
            / "deepswe"
            / f"deepswe-central-123-{task_name}"
            / f"{task_name}__trial"
        )
        _write_bundle(trial, task_directory="")

    report = audit_run_root(tmp_path)

    assert set(report["tasks"]) == {"alpha-fix", "beta-fix"}
