from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "eval" / "benchmark_product_contract.json"
AGENTS = ROOT / "AGENTS.md"
WORKFLOWS = {
    "terminal-bench-2": ROOT / ".github" / "workflows" / "tb2_miniswe_product.yml",
    "deepswe": ROOT / ".github" / "workflows" / "deepswe_gt_harness_product.yml",
    "swe-live-lite": ROOT / ".github" / "workflows" / "swe_live_lite_gt_harness_product.yml",
}
CERTIFICATION_WORKFLOW = ROOT / ".github" / "workflows" / "prerelease_product_matrix.yml"


def test_one_machine_contract_binds_all_benchmark_suites_to_one_product() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))

    assert contract["schema"] == "gt.benchmark_product_contract.v1"
    assert contract["product_command"] == "gt-harness run"
    assert contract["agent_scaffold"] == "mini-swe-agent"
    assert contract["agent_scaffold_version"] == "2.4.6"
    assert contract["retrieval_mode"] == "hybrid_required"
    assert set(contract["benchmark_suites"]) == set(WORKFLOWS)
    assert set(contract["required_receipts"]) >= {
        "benchmark-adapter.json",
        "gt-run.json",
        "gt-run.trajectory.json",
    }
    assert contract["delivery_invariant"]["allowed_graph_statuses"] == [
        "READY",
        "READY_WITH_DECLARED_LIMITATIONS",
    ]
    assert contract["delivery_invariant"]["query_ready"] is True


def test_full_linux_certification_runs_exact_sha_and_uploads_all_evidence() -> None:
    source = CERTIFICATION_WORKFLOW.read_text(encoding="utf-8")

    assert "ref: ${{ inputs.ref }}" in source
    assert 'test "$(git rev-parse HEAD)" = "${{ inputs.ref }}"' in source
    assert "scripts/codespaces_product_certification.sh" in source
    assert "if: always()" in source
    assert "gt-codespaces-certification/" in source


def test_all_current_benchmark_workflows_use_the_same_product_not_old_seams() -> None:
    forbidden = (
        "eval.gt_central_agent",
        "gt_mini_patch.py",
        "gt_headless_runner.py",
        "Nano",
        "nano",
        "MCP",
    )
    for suite, path in WORKFLOWS.items():
        source = path.read_text(encoding="utf-8")
        assert "gt-harness run" in source, suite
        assert "mini-swe-agent==2.4.6" in source, suite
        assert "GT_RETRIEVAL_MODE" in source, suite
        assert "hybrid_required" in source, suite
        assert "gt-run.json" in source, suite
        assert "gt-run.trajectory.json" in source, suite
        assert "benchmark-adapter.json" in source, suite
        assert "official-verifier-result.json" in source, suite
        for old_seam in forbidden:
            assert old_seam not in source, (suite, old_seam)


def test_swe_live_lite_adapter_executes_only_the_canonical_cli() -> None:
    from eval.swe_live_lite_gt_harness_adapter import build_product_argv

    argv = build_product_argv(
        instruction="Fix the repository bug.",
        root="/testbed",
        model="openai/stealth/ox-alpha",
        base_url="https://openrouter.ai/api/v1",
        task_id="owner__repo-123",
        source_sha="a" * 40,
        time_budget_seconds=5310,
        max_iterations=300,
        output="/logs/agent/gt-run.json",
        state_dir="/logs/agent/gt-state",
    )

    assert argv[:2] == ["gt-harness", "run"]
    assert "--treatment" in argv and argv[argv.index("--treatment") + 1] == "groundtruth"
    assert "--model" in argv and argv[argv.index("--model") + 1] == "openai/stealth/ox-alpha"
    assert "--root" in argv and argv[argv.index("--root") + 1] == "/testbed"
    assert "--task-id" in argv and argv[argv.index("--task-id") + 1] == "owner__repo-123"
    assert "gt_mini_patch" not in " ".join(argv)


def test_swe_live_lite_workflow_is_real_product_plus_official_verifier() -> None:
    source = WORKFLOWS["swe-live-lite"].read_text(encoding="utf-8")

    assert "benchmarks/data/swebench_live_lite.jsonl" in source
    assert "mode:" in source and "pilot" in source and "full" in source
    assert "starryzhang" in source
    assert "task_image_repo_digest" in source
    assert "eval.swe_live_lite_gt_harness_adapter" in source
    assert "python -m swebench.harness.run_evaluation" in source
    assert "agent_patch.diff" in source
    assert "gt.swe_live_lite_gt_harness_attestation.v1" in source
    assert "product_error:" in source
    assert "active_graph_not_ready:" in source
    assert "active_dense_not_ready:" in source


def test_agents_file_gives_the_exact_runtime_integration_contract() -> None:
    source = AGENTS.read_text(encoding="utf-8")

    for suite, path in WORKFLOWS.items():
        assert suite in source
        assert path.name in source
    for required in (
        "gt-harness run",
        "mini-swe-agent==2.4.6",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "GT_INDEX_BINARY",
        "GT_DENSE_MODEL_DIR",
        "GT_RETRIEVAL_MODE=hybrid_required",
        "benchmark-adapter.json",
        "gt-run.json",
        "gt-run.trajectory.json",
        "current_repo_SHA == graph_repo_SHA",
    ):
        assert required in source
    assert "Do not use Nano" in source
    assert "Do not use MCP" in source
    assert "Do not use `gt_mini_patch.py`" in source


def test_obsolete_live_lite_workflow_is_not_dispatchable() -> None:
    assert not (ROOT / ".github" / "workflows" / "swebench_live_lite_full.yml").exists()


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"verifier_result": {"rewards": {"reward": 1.0}}}, 1),
        ({"rewards": {"reward": 0}}, 0),
    ],
)
def test_official_result_normalizer_binds_runner_reward_to_product_receipt(
    tmp_path: Path, payload: dict[str, object], expected: int
) -> None:
    from scripts.standardize_benchmark_result import standardize_result

    root = tmp_path / "results"
    agent = root / "job" / "agent"
    agent.mkdir(parents=True)
    (agent / "gt-run.json").write_text(
        json.dumps({"schema": "gt.run_receipt.v1", "task_id": "task-a"}),
        encoding="utf-8",
    )
    result_path = root / "job" / "result.json"
    result_path.write_text(json.dumps(payload), encoding="utf-8")

    receipt = standardize_result(
        root=root,
        suite="deepswe",
        task_id="task-a",
        source_sha="a" * 40,
    )

    assert receipt["status"] == "GRADED"
    assert receipt["reward"] == expected
    assert receipt["solved"] is (expected == 1)
    assert receipt["runner_result_sha256"]
    assert (agent / "official-verifier-result.json").is_file()


def test_official_result_normalizer_prefers_harbor_aggregate_over_trial_result(
    tmp_path: Path,
) -> None:
    from scripts.standardize_benchmark_result import standardize_result

    root = tmp_path / "results"
    job = root / "job"
    agent = job / "agent"
    trial = job / "task__trial"
    agent.mkdir(parents=True)
    trial.mkdir(parents=True)
    (agent / "gt-run.json").write_text(
        json.dumps({"schema": "gt.run_receipt.v1", "task_id": "task-a"}),
        encoding="utf-8",
    )
    (job / "result.json").write_text(
        json.dumps({
            "n_total_trials": 1,
            "stats": {"evals": {"task": {"metrics": [{"reward": 1}]}}},
        }),
        encoding="utf-8",
    )
    (trial / "result.json").write_text(
        json.dumps({"task_id": "task-a", "trial_name": "task__trial"}),
        encoding="utf-8",
    )

    receipt = standardize_result(
        root=root,
        suite="deepswe",
        task_id="task-a",
        source_sha="a" * 40,
    )

    assert receipt["status"] == "ERROR"
    assert receipt["reward"] is None
