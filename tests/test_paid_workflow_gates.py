from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "deepswe_gt_harness_product_p0731.yaml"


def test_paid_workflow_requires_explicit_approval_before_provider_or_tasks() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    assert source.count("approve_paid_run:") == 2
    assert source.count("if: ${{ inputs.approve_paid_run == true }}") == 2
    assert "if: ${{ always() && inputs.approve_paid_run == true }}" in source
    assert '"paid_run_approval"' in source
    assert '"paid_run_approved": True' in source


def test_provider_gate_fails_closed_on_availability_without_logging_amounts() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "https://api.deepseek.com/user/balance" in source
    assert "https://api.deepseek.com/models" not in source
    assert 'payload.get("is_available") is True' in source
    assert '"balance_available": os.environ["SUCCESS"] == "true"' in source
    assert "total_balance" not in source
    assert "granted_balance" not in source
    assert "topped_up_balance" not in source
