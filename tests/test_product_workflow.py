from __future__ import annotations

from pathlib import Path

from scripts.validate_product_workflow import validate_workflow

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "deepswe_gt_harness_product.yml"


def test_product_workflow_is_reachable_pinned_and_provider_free() -> None:
    assert validate_workflow(WORKFLOW, root=ROOT) == []


def test_only_canonical_product_workflow_is_active() -> None:
    active = sorted(path.name for path in (ROOT / ".github" / "workflows").glob("*.yml"))
    assert active == ["deepswe_gt_harness_product.yml"]
