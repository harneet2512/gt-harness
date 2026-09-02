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


def test_product_workflow_rejects_bypassing_manifest_pin_resolution(
    tmp_path: Path,
) -> None:
    altered = tmp_path / "workflow.yml"
    altered.write_text(
        WORKFLOW.read_text(encoding="utf-8").replace(
            "${{ steps.product-pins.outputs.review_inbox_commit }}",
            "ac45a546cb3c39d5b8ce0f630b5c8ce2ef572685",
        )
        + "\n# steps.product-pins.outputs.review_inbox_commit\n",
        encoding="utf-8",
    )
    assert "product_manifest_pins_unreachable" in validate_workflow(altered, root=ROOT)


def test_only_closed_supported_workflow_set_is_active() -> None:
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
    assert "e56c7ef17eaffee36c80ff4dde4f0cd3991c4dcd" in provider_free
    assert "7bbbc9d0b7f02f8cdaab79ad82ee86884b738eb5" in provider_free
    assert "+refs/heads/*:refs/remotes/origin/*" in provider_free
    assert "PRODUCER_PATH=\"/opt/groundtruth/gt-index/gt-index\"" in provider_free
    assert "sha256sum --check --strict" in provider_free
    assert "git config --global user.email \"gt-harness-ci@example.invalid\"" in provider_free
    assert "git config core.hooksPath \"${GITHUB_WORKSPACE}/.githooks\"" in provider_free
    assert "python scripts/verify_feature_matrix.py" in provider_free
    assert "python scripts/gt_audit.py" in paid
    assert "python scripts/gt_live_gate.py" in paid
    assert "--require-complete-census" in paid
    assert "python -m scripts.attest_deepswe" in paid
    assert "cp gt_finalstand/feature_matrix.json attestation/feature-matrix.json" in paid
    assert "AUDIT_EXIT=0" in paid
    assert '--workflow-run-id "$GITHUB_RUN_ID"' in paid
    assert "attestation/gt-audit.json" in paid
    assert "attestation/gt-live-gate.json" in paid
    assert "attestation/feature-matrix.json" in paid
    assert "task_selection" not in paid
    assert "if: always()" in paid.split(
        "- name: Verify all DeepSWE outcomes and product receipts", 1
    )[1].split("- name: Upload final DeepSWE GT Harness attestation", 1)[0]
    assert "from gt_harness.runtime_receipts import" not in paid


def test_paid_smoke_requires_all_exact_task_image_digests_before_provider_gate() -> None:
    paid = PAID_WORKFLOW.read_text(encoding="utf-8")
    assert '"container_image": bundle_task["container_image"]' in paid
    assert '"container_digest": bundle_task["container_digest"]' in paid
    assert "image_digest_gate:" in paid
    assert "needs: [plan, readiness, image_digest_gate]" in paid
    assert "needs: [plan, readiness, image_digest_gate, provider_gate]" in paid
    assert "Verify all exact task-image manifests without provider access" in paid
    assert 'docker buildx imagetools inspect --raw "$IMAGE_REF"' in paid
    assert 'test "sha256:${OBSERVED}" = "${DIGEST}"' in paid
    assert "Pull and verify the exact task image" in paid
    assert 'docker pull "${SOURCE_IMAGE}@${SOURCE_DIGEST}"' in paid
    assert "image_cache:" not in paid
    assert "ghcr.io/" not in paid
    assert "secrets.OPENROUTER_API_KEY" not in paid.split(
        "  image_digest_gate:", 1
    )[1].split(
        "  provider_gate:", 1
    )[0]
