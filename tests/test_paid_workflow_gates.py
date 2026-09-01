from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "deepswe_gt_harness_product_p0731.yaml"


def test_paid_workflow_requires_explicit_approval_before_provider_or_tasks() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    assert source.count("approve_paid_run:") == 2
    assert source.count("if: ${{ inputs.approve_paid_run == true }}") == 2
    assert "if: ${{ always() && inputs.approve_paid_run == true }}" in source
    assert '"paid_run_approval"' in source


def test_provider_route_is_loaded_once_and_preflight_precedes_matrix() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    route = json.loads((ROOT / "config" / "provider_route.v1.json").read_text())

    assert "scripts.provider_preflight" in source
    assert "config/provider_route.v1.json" in source
    assert "needs: [plan, provider_gate]" in source
    assert route["model"] not in source
    assert route["base_url"] not in source
    assert "DEEPSEEK_API_KEY" not in source
    assert "secrets.OPENROUTER_API_KEY" in source
    assert "total_credits" not in source
    assert "total_usage" not in source


def test_paid_workflow_stages_and_installs_the_exact_treatment_bundle() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    assert '"GT_GROUNDTRUTH_WHEEL_HOST":' in source
    assert '"GT_INDEX_BINARY_HOST":' in source
    assert '"GT_HARNESS_WHEEL_HOST":' in source
    assert '"GT_HARNESS_WHEEL_SHA256":' in source
    assert 'Path(os.environ["GITHUB_ENV"]).open' in source
    assert 'python -m pip install --disable-pip-version-check --no-deps "$GT_HARNESS_WHEEL_HOST"' in source
