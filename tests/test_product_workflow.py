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
