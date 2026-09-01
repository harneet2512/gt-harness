from __future__ import annotations

import hashlib
import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from gt_harness.product import (
    BundleError,
    _assert_committed_source_closure,
    aggregate_results,
    build_product_bundle,
    project_task_environment,
    run_provider_free_acceptance,
    validate_product_bundle,
)

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "config" / "deepswe_product_bundle_v1.json"


def test_operator_entrypoint_is_direct_script_reachable_outside_checkout(
    tmp_path: Path,
) -> None:
    process = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "gt_product_acceptance.py"), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert process.returncode == 0, process.stderr
    assert "--fake-provider" in process.stdout


def test_shipping_adapter_and_every_manifest_task_are_reachable() -> None:
    module = importlib.import_module("eval.pier_gt_harness_adapter")
    adapter = module.PierGtHarnessMiniSwe246Agent
    assert adapter.MINISWE_AGENT_VERSION == "2.4.6"

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    installer = importlib.import_module("eval.miniswe_agent")
    assert manifest["schema"] == "gt.product_bundle_source.v1"
    assert manifest["miniswe_agent_version"] == "2.4.6"
    assert installer._GT_WHEEL_SHA256 == manifest["groundtruth"]["wheel_sha256"]
    assert installer._GT_BINARY_SHA256 == manifest["groundtruth"]["producer_sha256"]
    assert installer._UV_INSTALLER_SHA256 == manifest["uv"]["installer_sha256"]
    tasks = manifest["tasks"]
    assert len(tasks) == len({row["task_id"] for row in tasks})
    assert [row["ordinal"] for row in tasks] == list(range(1, len(tasks) + 1))
    for row in tasks:
        assert len(row["task_config_sha256"]) == 64


def test_bundle_is_reproducible_and_tamper_evident(tmp_path: Path) -> None:
    first = build_product_bundle(MANIFEST, output_dir=tmp_path / "first")
    second = build_product_bundle(MANIFEST, output_dir=tmp_path / "second")
    assert first == second
    assert first["schema"] == "gt.product_bundle.v1"
    assert first["bundle_digest_sha256"] == second["bundle_digest_sha256"]
    validate_product_bundle(first, root=ROOT)

    altered = json.loads(json.dumps(first))
    altered["miniswe_agent_version"] = "2.3.0"
    with pytest.raises(BundleError, match="bundle_digest_mismatch"):
        validate_product_bundle(altered, root=ROOT)


def test_bundle_source_closure_rejects_uncommitted_bytes(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "fixture@example.invalid"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Fixture"], cwd=tmp_path, check=True
    )
    source = tmp_path / "product.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "product.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=tmp_path, check=True)
    _assert_committed_source_closure(tmp_path, ["product.py"])

    source.write_text("VALUE = 2\n", encoding="utf-8")
    with pytest.raises(BundleError, match="source_closure_differs_from_head"):
        _assert_committed_source_closure(tmp_path, ["product.py"])


def test_task_environment_is_closed_and_never_contains_credentials() -> None:
    host = {
        "GT_RL_PROFILE": "2",
        "GT_RETRIEVAL_MODE": "hybrid_required",
        "GT_INDEX_BINARY": "/safe/gt-index",
        "GT_EVIL": "must-not-pass",
        "OPENAI_API_KEY": "secret-openai",
        "DEEPSEEK_API_KEY": "secret-deepseek",
        "GOOGLE_APPLICATION_CREDENTIALS": "secret-gcp",
        "GITHUB_TOKEN": "secret-github",
    }
    projected = project_task_environment(host, treatment="groundtruth")
    assert projected == {
        "GT_INDEX_BINARY": "/safe/gt-index",
        "GT_RETRIEVAL_MODE": "hybrid_required",
        "GT_RL_PROFILE": "2",
        "GT_TREATMENT": "groundtruth",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    }
    assert not any("KEY" in name or "TOKEN" in name or "CREDENTIAL" in name for name in projected)


def test_summary_is_exact_once_and_conservative() -> None:
    plan = {
        "schema": "gt.benchmark_plan.v1",
        "tasks": [{"task_id": "a"}, {"task_id": "b"}, {"task_id": "c"}],
    }
    complete = {
        "schema": "gt.benchmark_task_result.v1",
        "task_id": "a",
        "status": "complete",
        "grader": {"solved": True},
        "honesty": {
            "schema": "gt.honesty_envelope.v1",
            "source_revision": "fixture",
            "workspace_revision": "a",
            "completeness": "complete",
            "returned_count": 1,
            "true_total": 1,
            "ambiguities": [],
            "unresolved_identities": [],
            "payload": None,
            "abstention_reason": None,
        },
    }
    timed_out = {
        "schema": "gt.benchmark_task_result.v1",
        "task_id": "b",
        "status": "incomplete",
        "stop_reason": "task_timeout",
        "grader": {"solved": None},
        "honesty": {
            "schema": "gt.honesty_envelope.v1",
            "source_revision": "fixture",
            "workspace_revision": "b",
            "completeness": "incomplete",
            "returned_count": 0,
            "true_total": None,
            "ambiguities": [],
            "unresolved_identities": [],
            "payload": None,
            "abstention_reason": "task_timeout",
        },
    }
    summary = aggregate_results(plan, [complete, timed_out])
    assert summary["schema"] == "gt.benchmark_summary.v1"
    assert summary["planned"] == 3
    assert summary["solved"] == 1
    assert summary["failed"] == 2
    assert [row["task_id"] for row in summary["results"]] == ["a", "b", "c"]
    assert summary["results"][2]["stop_reason"] == "missing_result"

    missing_honesty = {key: value for key, value in complete.items() if key != "honesty"}
    malformed = aggregate_results(plan, [missing_honesty, timed_out])
    assert malformed["solved"] == 0
    assert malformed["results"][0]["stop_reason"] == "malformed_honesty_envelope"
    assert malformed["results"][0]["honesty"]["true_total"] is None

    with pytest.raises(ValueError, match="duplicate_task_result:a"):
        aggregate_results(plan, [complete, complete])


def test_provider_free_acceptance_executes_both_parity_arms(tmp_path: Path) -> None:
    receipt = run_provider_free_acceptance(MANIFEST, output_dir=tmp_path)
    assert receipt["schema"] == "gt.product_closeout.v1"
    assert receipt["status"] == "VERIFIED_PROVIDER_FREE"
    assert receipt["provider_calls"] == 0
    assert receipt["release_eligible"] is False
    assert "container_install_not_executed" in receipt["release_blockers"]
    assert [row["arm"] for row in receipt["arms"]] == ["bare", "groundtruth"]
    assert receipt["parity"]["structural_identity_equal"] is True
    assert receipt["secret_canary_matches"] == []
    install = json.loads((tmp_path / "install-attestation.json").read_text(encoding="utf-8"))
    assert install["schema"] == "gt.install_attestation.v1"
    assert install["smoke_checks"]["source_checkout_not_imported"] is True
    canonical = json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert hashlib.sha256(canonical.encode("utf-8")).hexdigest()
