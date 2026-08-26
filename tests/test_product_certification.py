from __future__ import annotations

import json
import subprocess
from pathlib import Path

from gt_harness.product_certification import (
    REQUIRED_RECEIPTS,
    REQUIRED_STEPS,
    certify_receipt_bundle,
    load_product_surface,
    validate_product_surface,
)


def _write(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def test_product_surface_is_typed_and_rejects_unexpected_workflow(tmp_path: Path) -> None:
    (tmp_path / "production-surface.toml").write_text(
        """
schema = "gt.product_surface.v1"
python_modules = ["app.cli"]
console_entry_points = ["app=app.cli:main"]
benchmark_adapters = ["app.cli"]
dispatchable_workflows = ["product.yml"]
forbidden_modules = ["app.legacy"]
[schemas]
graph_receipt = "gt.graph_receipt.v5"
[budgets]
initial_tokens = 500
[languages]
structural_certification_candidates = ["python"]
semantic_certification_candidates = ["python"]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "cli.py").write_text("def main(): pass\n", encoding="utf-8")
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "product.yml").write_text("name: product\n", encoding="utf-8")

    surface = load_product_surface(tmp_path)
    assert surface.python_modules == ("app.cli",)
    assert validate_product_surface(tmp_path) == ()

    (workflows / "legacy.yml").write_text("name: legacy\n", encoding="utf-8")
    errors = validate_product_surface(tmp_path)
    assert [(error.code, error.message) for error in errors] == [
        ("surface_workflow_unexpected", "legacy.yml")
    ]


def _bundle(tmp_path: Path, repository: Path) -> Path:
    bundle = tmp_path / "bundle"
    common = {
        "status": "PASS",
        "provider_calls": 0,
        "provider_credentials_inspected": False,
    }
    matrix_rows = [
        {
            "name": f"repo-{index}",
            "build_status": "READY",
            "query_ready": True,
            "files_failed": 0,
        }
        for index in range(10)
    ]
    values: dict[str, dict[str, object]] = {
        "real-repository-matrix.json": {**common, "repositories": matrix_rows},
        "graph-truth.json": {
            **common,
            "aggregate": {"sample_size": 62, "precision": 1.0, "recall": 1.0},
        },
        "graph-lifecycle.json": {
            **common,
            "cases": [{"status": "PASS"} for _ in range(9)],
        },
        "language-lifecycle.json": {
            **common,
            "languages": [
                {"language": language, "status": "PASS"}
                for language in ("python", "javascript", "typescript", "go", "rust", "java")
            ],
        },
        "harness-e2e.json": {
            **common,
            "agent_scaffold_version": "2.4.6",
            "same_observation": True,
            "context_schema_v7": True,
            "raw_output_preserved": True,
            "trajectory_delivery_receipt_preserved": True,
            "restart_reused_current_graph": True,
            "retrieval_mode": "hybrid_required",
            "dense_lifecycle_ready": True,
            "dense_queries": [{"query_ready": True, "candidate_count": 3}],
            "initial_context_token_count": 500,
            "update_context_token_count": 350,
            "total_context_token_count": 850,
            "provider_delivery_receipts": [
                {
                    "delivered_before_call": 1,
                    "serialized_claim_ids": ["claim-1"],
                },
                {
                    "delivered_before_call": 2,
                    "serialized_claim_ids": ["claim-2"],
                },
            ],
            "delivered_claim_ids": ["claim-1", "claim-2"],
            "delivery_reconciliation": "PASS",
        },
        "failure-campaign.json": {
            **common,
            "cases": [{"status": "PASS"} for _ in range(18)],
        },
        "localization-truth.json": {
            **common,
            "summary": {
                "schema": "gt.localization_truth_report.v2",
                "retrieval_mode": "hybrid_required",
                "cases_expected": 20,
                "cases_run": 20,
                "case_failures": [],
                "missing_oracle_tasks": [],
                "extra_oracle_tasks": [],
                "tasks_with_false_edit_authority": [],
                "tasks_below_half_required_coverage": [],
                "treatment_failures": [],
                "dense_not_ready_tasks": [],
                "mean_exact_edit_precision": 1.0,
                "mean_required_facet_coverage": 0.9,
                "implementation_role_precision": 0.9,
            },
        },
        "product-surface.json": {
            **common,
            "python_modules": ["gt_harness.cli"],
            "dispatchable_workflows": ["prerelease_product_matrix.yml"],
            "forbidden_modules": ["groundtruth"],
            "errors": [],
        },
        "verification-summary.json": {
            **common,
            "doctor_exit_code": 0,
            "build_exit_code": 0,
            "query_exit_code": 0,
            "stale_status_exit_code": 1,
            "rebuild_exit_code": 0,
            "immutable_generation_changed": True,
            "temporary_state_cleaned": True,
        },
    }
    for filename, schema in REQUIRED_RECEIPTS.items():
        _write(bundle / "receipts" / filename, {"schema": schema, **values[filename]})
    commit = _git(repository, "rev-parse", "HEAD")
    _write(
        bundle / "codespaces-product-certification.json",
        {
            "schema": "gt.codespaces_product_certification.v1",
            **common,
            "commit_sha": commit,
            "working_tree_state": "clean",
            "platform": "Linux-test-x86_64",
            "steps": [{"name": name, "status": "PASS"} for name in REQUIRED_STEPS],
        },
    )
    return bundle


def test_certification_accepts_complete_exact_provider_free_bundle(
    tmp_path: Path, monkeypatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.email", "audit@example.invalid"], cwd=repository, check=True
    )
    subprocess.run(["git", "config", "user.name", "Audit"], cwd=repository, check=True)
    (repository / "README.md").write_text("receipt subject\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "subject"], cwd=repository, check=True)
    bundle = _bundle(tmp_path, repository)
    monkeypatch.setattr("gt_harness.product_certification.platform.platform", lambda: "Linux-test")

    result = certify_receipt_bundle(bundle, repository=repository)

    assert result["status"] == "CERTIFIED_WITH_DECLARED_LIMITATIONS"
    assert result["errors"] == []


def test_certification_rejects_wrong_sha_provider_use_and_missing_receipt(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.email", "audit@example.invalid"], cwd=repository, check=True
    )
    subprocess.run(["git", "config", "user.name", "Audit"], cwd=repository, check=True)
    (repository / "README.md").write_text("receipt subject\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "subject"], cwd=repository, check=True)
    bundle = _bundle(tmp_path, repository)
    wrapper = json.loads((bundle / "codespaces-product-certification.json").read_text())
    wrapper["commit_sha"] = "0" * 40
    _write(bundle / "codespaces-product-certification.json", wrapper)
    truth = json.loads((bundle / "receipts" / "graph-truth.json").read_text())
    truth["provider_calls"] = 1
    _write(bundle / "receipts" / "graph-truth.json", truth)
    (bundle / "receipts" / "failure-campaign.json").unlink()

    result = certify_receipt_bundle(bundle, repository=repository)

    assert result["status"] == "NOT_CERTIFIED"
    assert {error["code"] for error in result["errors"]} >= {
        "evidence_sha_mismatch",
        "provider_use",
        "receipt_missing",
    }


def test_certification_rejects_sparse_only_or_empty_dense_product_e2e(
    tmp_path: Path, monkeypatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.email", "audit@example.invalid"],
        cwd=repository,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "Audit"], cwd=repository, check=True)
    (repository / "README.md").write_text("receipt subject\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "subject"], cwd=repository, check=True)
    bundle = _bundle(tmp_path, repository)
    e2e_path = bundle / "receipts" / "harness-e2e.json"
    e2e = json.loads(e2e_path.read_text(encoding="utf-8"))
    e2e["retrieval_mode"] = "sparse_only"
    e2e["dense_lifecycle_ready"] = False
    e2e["dense_queries"] = [{"query_ready": True, "candidate_count": 0}]
    e2e["context_schema_v7"] = False
    e2e["delivered_claim_ids"] = ["claim-not-serialized"]
    e2e["provider_delivery_receipts"][1]["delivered_before_call"] = 1
    _write(e2e_path, e2e)
    monkeypatch.setattr("gt_harness.product_certification.platform.platform", lambda: "Linux-test")

    result = certify_receipt_bundle(bundle, repository=repository)

    assert {error["code"] for error in result["errors"]} >= {
        "harness_retrieval_mode",
        "harness_dense_lifecycle",
        "harness_dense_query",
        "harness_context_schema",
        "harness_delivery_reconciliation",
        "harness_provider_call_timing",
    }


def test_codespaces_campaign_provisions_public_pinned_dense_asset() -> None:
    script = (
        Path(__file__).resolve().parents[1] / "scripts" / "codespaces_product_certification.sh"
    ).read_text(encoding="utf-8")

    assert "gh release download" not in script
    assert (
        "https://github.com/harneet2512/gt-harness/releases/download/gt-retrieval-runtime-v1"
    ) in script
    assert "curl --fail --location --silent --show-error" in script
    assert "564e6c65ee0c739a486702e9e3e9b33c3f697c19c34dbe886bce9eec497ce971" in script
    assert "91f1def9b9391fdabe028cd3f3fcc4efd34e5d1f08c3bf2de513ebb5911a1854" in script
