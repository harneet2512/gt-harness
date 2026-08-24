from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "deepswe_gt_harness_product.yml"
ENTRY_WORKFLOW = ROOT / ".github" / "workflows" / "deepswe_miniswe_central.yml"
MANIFEST = ROOT / "eval" / "deepswe_smoke20_v1.json"


def test_deepswe_product_adapter_is_the_real_gt_harness_boundary() -> None:
    from eval.pier_gt_harness_adapter import PierGtHarnessMiniSwe228Agent

    assert PierGtHarnessMiniSwe228Agent.name() == "gt-harness-miniswe-2.2.8"
    adapter_source = (ROOT / "eval" / "pier_gt_harness_adapter.py").read_text(
        encoding="utf-8"
    )
    for domain in ("openrouter.ai", "pypi.org", "files.pythonhosted.org"):
        assert domain in adapter_source
    pytest.importorskip("pier.models.agent.network")
    allowlist = PierGtHarnessMiniSwe228Agent.network_allowlist(
        PierGtHarnessMiniSwe228Agent.__new__(PierGtHarnessMiniSwe228Agent)
    )
    assert "openrouter.ai" in allowlist.domains
    assert "pypi.org" in allowlist.domains
    assert "files.pythonhosted.org" in allowlist.domains


def test_deepswe_smoke20_manifest_is_balanced_frozen_and_baseline_bound() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    tasks = payload["task_ids"]

    assert payload["schema"] == "gt.deepswe_smoke20.v1"
    assert payload["benchmark_sha"] == "435ee89ec2f2e2289f33b0da4f992f0b7b7266b9"
    assert payload["baseline"]["run_id"] == "32615305543"
    assert payload["baseline"]["model"] == "openrouter/stealth/ox-alpha"
    assert payload["baseline"]["mini_swe_agent_version"] == "2.4.6"
    assert len(tasks) == 20
    assert len(set(tasks)) == 20
    assert payload["language_counts"] == {
        "go": 4,
        "javascript": 4,
        "python": 4,
        "rust": 4,
        "typescript": 4,
    }
    canonical = "\n".join(tasks) + "\n"
    assert hashlib.sha256(canonical.encode()).hexdigest() == payload["task_order_sha256"]
    assert "awilix-async-container-initialization" in tasks
    assert "boa-hierarchical-evaluation-cancellation" in tasks


def test_deepswe_product_workflow_runs_and_attests_the_current_product() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in source
    assert "workflow_call:" in source
    assert "eval/deepswe_smoke20_v1.json" in source
    assert "repository: datacurve-ai/deep-swe" in source
    assert "435ee89ec2f2e2289f33b0da4f992f0b7b7266b9" in source
    assert "eval.pier_gt_harness_adapter:PierGtHarnessMiniSwe228Agent" in source
    assert "mini-swe-agent==2.2.8" in source
    assert "datacurve-pier==0.3.1" in source
    assert "stealth/ox-alpha" in source
    assert "secrets.OPENROUTER_NEW" in source
    assert "provider_gate:" in source
    assert "gt-harness run" in source
    assert "gt-run.json" in source
    assert "gt-run.trajectory.json" in source
    assert "benchmark-adapter.json" in source
    assert "product_error:" in source
    assert "active_graph_not_ready:" in source
    assert "active_dense_not_ready:" in source
    assert "trial_task_set_mismatch" in source
    assert "gt.deepswe_gt_harness_attestation.v1" in source
    assert "max-parallel: 4" in source
    assert "eval.pier_gt_adapter:PierMiniSweCentralAgent" not in source
    assert "eval.gt_central_agent" not in source
    assert "nano" not in source.lower()


def test_registered_deepswe_entrypoint_dispatches_only_the_product_workflow() -> None:
    source = ENTRY_WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in source
    assert "uses: ./.github/workflows/deepswe_gt_harness_product.yml" in source
    assert "secrets: inherit" in source
    assert "eval.pier_gt_adapter" not in source
    assert "eval.gt_central_agent" not in source
    assert "DeepSWE Mini-SWE central evaluation" not in source
