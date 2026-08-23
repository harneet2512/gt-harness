from __future__ import annotations

import json
import subprocess
from pathlib import Path

from gt_harness.product_certification import (
    REQUIRED_RECEIPTS,
    REQUIRED_STEPS,
    certify_receipt_bundle,
)


def _write(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


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
            "agent_scaffold_version": "2.2.8",
            "same_observation": True,
            "raw_output_preserved": True,
            "restart_reused_current_graph": True,
            "retrieval_mode": "hybrid_required",
            "dense_lifecycle_ready": True,
            "dense_queries": [{"query_ready": True, "candidate_count": 3}],
            "initial_context_token_count": 500,
            "update_context_token_count": 350,
        },
        "failure-campaign.json": {
            **common,
            "cases": [{"status": "PASS"} for _ in range(18)],
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
    subprocess.run(
        ["git", "config", "user.name", "Audit"], cwd=repository, check=True
    )
    (repository / "README.md").write_text("receipt subject\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "subject"], cwd=repository, check=True)
    bundle = _bundle(tmp_path, repository)
    e2e_path = bundle / "receipts" / "harness-e2e.json"
    e2e = json.loads(e2e_path.read_text(encoding="utf-8"))
    e2e["retrieval_mode"] = "sparse_only"
    e2e["dense_lifecycle_ready"] = False
    e2e["dense_queries"] = [{"query_ready": True, "candidate_count": 0}]
    _write(e2e_path, e2e)
    monkeypatch.setattr("gt_harness.product_certification.platform.platform", lambda: "Linux-test")

    result = certify_receipt_bundle(bundle, repository=repository)

    assert {error["code"] for error in result["errors"]} >= {
        "harness_retrieval_mode",
        "harness_dense_lifecycle",
        "harness_dense_query",
    }
