from __future__ import annotations

from pathlib import Path

from scripts.validate_product_workflow import validate_workflow

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "deepswe_gt_harness_product.yml"
PAID_WORKFLOW = (
    ROOT / ".github" / "workflows" / "deepswe_gt_harness_product_p0731.yaml"
)


def test_product_workflow_is_reachable_pinned_and_provider_free() -> None:
    assert validate_workflow(WORKFLOW, root=ROOT) == []


def test_only_canonical_product_workflow_is_active() -> None:
    active = sorted(
        path.name
        for pattern in ("*.yml", "*.yaml")
        for path in (ROOT / ".github" / "workflows").glob(pattern)
    )
    assert active == [
        "deepswe_gt_harness_product.yml",
        "deepswe_gt_harness_product_p0731.yaml",
    ]


def test_paid_product_workflow_is_reachable_pinned_and_approval_gated() -> None:
    assert validate_workflow(PAID_WORKFLOW, root=ROOT) == []


def test_readiness_workflows_enforce_full_suite_pinned_sources_and_dark_gate() -> None:
    provider_free = WORKFLOW.read_text(encoding="utf-8")
    paid = PAID_WORKFLOW.read_text(encoding="utf-8")
    assert "python -m pytest -q -ra tests" in provider_free
    assert "repository: abhigyanpatwari/GitNexus" in provider_free
    assert "ref: 7e993ab8972386294fb96bf14a8665d0b5325397" in provider_free
    assert "fetch-depth: 0" in provider_free
    assert "PRODUCER_PATH=\"${RUNNER_TEMP}/gt-index\"" in provider_free
    assert "GT_INDEX_BINARY=%s" in provider_free
    assert "sha256sum --check --strict" in provider_free
    assert "git config --global user.email \"gt-harness-ci@example.invalid\"" in provider_free
    assert "git config core.hooksPath \"${GITHUB_WORKSPACE}/.githooks\"" in provider_free
    assert "python scripts/verify_feature_matrix.py" in provider_free
    assert "python scripts/gt_audit.py" in paid
    assert "python scripts/gt_live_gate.py" in paid
    assert "--require-complete-census" in paid
    assert "python -m scripts.attest_deepswe" in paid
    assert "from gt_harness.runtime_receipts import" not in paid
