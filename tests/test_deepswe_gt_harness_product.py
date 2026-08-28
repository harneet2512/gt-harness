from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "deepswe_gt_harness_product.yml"
MANIFEST = ROOT / "eval" / "deepswe_smoke20_v1.json"


def test_deepswe_product_adapter_is_the_real_gt_harness_boundary() -> None:
    from eval.pier_gt_harness_adapter import PierGtHarnessMiniSwe246Agent

    assert PierGtHarnessMiniSwe246Agent.name() == "gt-harness-miniswe-2.4.6"
    adapter_source = (ROOT / "eval" / "pier_gt_harness_adapter.py").read_text(
        encoding="utf-8"
    )
    for domain in (
        ".githubusercontent.com",
        "api.deepseek.com",
        "astral.sh",
        "files.pythonhosted.org",
        "github.com",
        "pypi.org",
        "releases.astral.sh",
    ):
        assert domain in adapter_source
    pytest.importorskip("pier.models.agent.network")
    allowlist = PierGtHarnessMiniSwe246Agent.network_allowlist(
        PierGtHarnessMiniSwe246Agent.__new__(PierGtHarnessMiniSwe246Agent)
    )
    assert set(allowlist.domains) == {
        ".githubusercontent.com",
        "api.deepseek.com",
        "astral.sh",
        "files.pythonhosted.org",
        "github.com",
        "pypi.org",
        "releases.astral.sh",
    }


@pytest.mark.asyncio
async def test_pier_adapter_scopes_install_and_product_exec_through_egress_proxy(
    tmp_path: Path,
) -> None:
    from eval.pier_gt_harness_adapter import PierGtHarnessMiniSwe246Agent

    observed: list[dict[str, str]] = []

    class PierEnvironment:
        def agent_process_env(self, env):
            return {
                **dict(env or {}),
                "HTTPS_PROXY": "http://agent:ephemeral-token@pier-egress-proxy:8080",
            }

        async def exec(self, command, *, env=None, **kwargs):
            observed.append(dict(env or {}))
            return SimpleNamespace(return_code=0, stdout="", stderr="")

    environment = PierEnvironment()
    agent = PierGtHarnessMiniSwe246Agent(logs_dir=tmp_path / "logs")

    await agent.exec_as_agent(environment, "curl -fsSL https://astral.sh/")
    await agent._exec_product_secret_safe(environment, "gt-harness run", {})

    assert len(observed) == 2
    assert all(
        env.get("HTTPS_PROXY", "").endswith("@pier-egress-proxy:8080")
        for env in observed
    )


def test_deepswe_smoke20_manifest_is_balanced_frozen_and_baseline_bound() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    tasks = payload["task_ids"]

    assert payload["schema"] == "gt.deepswe_smoke20.v1"
    assert payload["benchmark_sha"] == "435ee89ec2f2e2289f33b0da4f992f0b7b7266b9"
    assert payload["baseline"]["comparison_protocol"] == "paired_same_workflow_v1"
    assert payload["baseline"]["model"] == "deepseek-v4-flash"
    assert payload["baseline"]["provider"] == "deepseek:native:api.deepseek.com"
    assert payload["baseline"]["required_treatment"] == "bare"
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
    provider_gate = source.split("  provider_gate:", 1)[1].split("  task:", 1)[0]
    attest = source.split("  attest:", 1)[1]

    assert "workflow_dispatch:" in source
    assert "workflow_call:" in source
    assert "eval/deepswe_smoke20_v1.json" in source
    assert "repository: datacurve-ai/deep-swe" in source
    assert "435ee89ec2f2e2289f33b0da4f992f0b7b7266b9" in source
    assert "eval.pier_gt_harness_adapter:PierGtHarnessMiniSwe246Agent" in source
    assert "mini-swe-agent==2.4.6" in source
    assert "datacurve-pier==0.3.1" in source
    assert "deepseek-v4-flash" in source
    assert "secrets.DEEPSEEK_API_KEY" in source
    assert "https://api.deepseek.com" in source
    assert "if python -c" in source
    assert 'assert "deepseek-v4-flash" in models' in source
    assert "stealth/ox-alpha" not in source
    assert "secrets.OPENROUTER_NEW" not in source
    assert "from gt_harness" not in provider_gate
    assert "uses: actions/checkout@v4" in attest
    assert "ref: ${{ needs.plan.outputs.source_sha }}" in attest
    assert 'rsplit("/", 1)' in attest
    assert "secrets.DOCKERHUB_USERNAME" in source
    assert "secrets.DOCKERHUB_TOKEN" in source
    assert "secrets.DOCKERHUB_USERNAME_ROTATION" in source
    assert "secrets.DOCKERHUB_TOKEN_ROTATION" in source
    assert "Stagger Docker task-image pulls" in source
    assert "max-parallel: 20" in source
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
    assert "max-parallel: 20" in source
    assert "eval.pier_gt_adapter:PierMiniSweCentralAgent" not in source
    assert "eval.gt_central_agent" not in source
    assert "nano" not in source.lower()


def test_deepswe_attestation_uses_role_phase_delivery_budgets() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    assert 'token_limit = 500 if delivery.get("kind") == "repository_start" else 350' in source
    assert 'context_token_count") or 0) > token_limit' in source
    assert "total_context_budget_exceeded" in source
    assert 'context_token_count") or 0) > 350' not in source


def test_deepswe_product_is_the_only_dispatchable_deepswe_entrypoint() -> None:
    workflows = ROOT / ".github" / "workflows"

    assert WORKFLOW.is_file()
    assert not (workflows / "deepswe_miniswe_central.yml").exists()
    assert [path.name for path in workflows.glob("*deepswe*.yml")] == [WORKFLOW.name]
