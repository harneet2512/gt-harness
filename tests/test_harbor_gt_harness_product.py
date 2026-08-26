from __future__ import annotations

import ast
import asyncio
import hashlib
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from harbor.agents.installed.base import NonZeroAgentExitCodeError
from harbor.models.agent.context import AgentContext

from eval.harbor_gt_harness_adapter import (
    CANONICAL_MINISWE_VERSION,
    GtHarnessMiniSwe246Agent,
)

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/tb2_miniswe_product.yml"
CLEANUP_LEDGER = ROOT / "docs/GT_HARBOR_CLEANUP_LEDGER.md"

REPAIR20_TASKS = (
    "cobol-modernization",
    "count-dataset-tokens",
    "extract-elf",
    "feal-linear-cryptanalysis",
    "fix-code-vulnerability",
    "headless-terminal",
    "largest-eigenval",
    "llm-inference-batching-scheduler",
    "mcmc-sampling-stan",
    "portfolio-optimization",
    "prove-plus-comm",
    "qemu-alpine-ssh",
    "regex-chess",
    "sanitize-git-repo",
    "schemelike-metacircular-eval",
    "torch-pipeline-parallelism",
    "torch-tensor-parallelism",
    "video-processing",
    "winning-avg-corewars",
    "write-compressor",
)
REPAIR20_SHA256 = "36d5c8945f6f8d9ae23fe2cea759f16da0c0cea424a98f710cfaa0d9d6fd0303"


@pytest.mark.asyncio
async def test_harbor_adapter_runs_the_production_product_with_a_model_identity_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "  test-key\n")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
    commands: list[tuple[str, dict[str, str]]] = []
    log_records: list[object] = []

    class Environment:
        async def exec(self, command, *, env=None, **kwargs):
            commands.append((command, dict(env or {})))
            return SimpleNamespace(return_code=0, stdout="", stderr="")

    agent = GtHarnessMiniSwe246Agent(
        logs_dir=tmp_path / "logs",
        model_name="stealth/ox-alpha",
        task_id="fix-code-vulnerability",
        product_source_sha="a" * 40,
        time_budget_seconds="840",
    )
    monkeypatch.setattr(
        agent.logger,
        "debug",
        lambda *args, **kwargs: log_records.append((args, kwargs)),
    )

    await agent.run("Repair the repository.", Environment(), AgentContext())

    assert CANONICAL_MINISWE_VERSION == "2.4.6"
    assert len(commands) == 1
    command, environment = commands[0]
    assert "gt-harness\" run" in command
    assert "--treatment groundtruth" in command
    assert "--root \"$PWD\"" in command
    assert "--state-dir /logs/agent/gt-state" in command
    assert "--output /logs/agent/gt-run.json" in command
    assert "--task-id fix-code-vulnerability" in command
    assert "--trial-id 1" in command
    assert "eval.gt_central_agent" not in command
    assert "miniswe_gt_run" not in command
    assert " mcp " not in command.lower()
    assert environment["OPENAI_API_KEY"] == "test-key"
    assert environment["OPENAI_BASE_URL"] == "https://openrouter.ai/api/v1"
    assert environment["GT_REQUESTED_MODEL"] == "stealth/ox-alpha"
    assert environment["GT_EFFECTIVE_MODEL"] == "openai/stealth/ox-alpha"
    assert environment["GT_RETRIEVAL_MODE"] == "hybrid_required"
    assert environment["GT_DENSE_MODEL_DIR"] == "/installed-agent/snowflake-arctic-embed-m"
    assert environment["GT_HARBOR_TIME_BUDGET_SECONDS"] == "840"
    assert '"time_budget_seconds": "840"' in command
    assert "test-key" not in command
    assert "/logs/agent/benchmark-adapter.json" in command
    assert all("test-key" not in repr(record) for record in log_records)


@pytest.mark.asyncio
async def test_harbor_adapter_can_run_the_matched_bare_control_arm(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GT_TREATMENT", "bare")
    commands: list[str] = []

    class Environment:
        async def exec(self, command, *, env=None, **kwargs):
            commands.append(command)
            return SimpleNamespace(return_code=0, stdout="", stderr="")

    agent = GtHarnessMiniSwe246Agent(
        logs_dir=tmp_path / "logs",
        model_name="stealth/ox-alpha",
        task_id="matched-control",
        product_source_sha="b" * 40,
        time_budget_seconds="840",
    )
    await agent.run("Run the matched control.", Environment(), AgentContext())

    assert len(commands) == 1
    assert "--treatment bare" in commands[0]
    assert '"treatment": "bare"' in commands[0]


@pytest.mark.asyncio
async def test_harbor_adapter_finalizes_a_sigkill_checkpoint_before_raising(
    tmp_path: Path,
) -> None:
    commands: list[str] = []

    class Environment:
        async def exec(self, command, *, env=None, **kwargs):
            commands.append(command)
            if len(commands) == 1:
                return SimpleNamespace(return_code=137, stdout="", stderr="")
            return SimpleNamespace(return_code=0, stdout="", stderr="")

    agent = GtHarnessMiniSwe246Agent(logs_dir=tmp_path / "logs")

    with pytest.raises(NonZeroAgentExitCodeError, match="exit 137"):
        await agent._exec_product_secret_safe(Environment(), "product", {})

    assert len(commands) == 2
    assert "gt_harness.supervision" in commands[1]
    assert "--return-code 137" in commands[1]
    assert "/logs/agent/gt-run.json" in commands[1]
    assert "/logs/agent/gt-run.trajectory.json" in commands[1]
    assert "--termination-kind PROCESS_EXIT" in commands[1]


@pytest.mark.asyncio
async def test_harbor_adapter_finalizes_checkpoint_when_harbor_cancels_it(
    tmp_path: Path,
) -> None:
    commands: list[str] = []

    class Environment:
        async def exec(self, command, *, env=None, **kwargs):
            commands.append(command)
            if len(commands) == 1:
                raise asyncio.CancelledError
            return SimpleNamespace(return_code=0, stdout="", stderr="")

    agent = GtHarnessMiniSwe246Agent(logs_dir=tmp_path / "logs")

    with pytest.raises(asyncio.CancelledError):
        await agent._exec_product_secret_safe(Environment(), "product", {})

    assert len(commands) == 2
    assert "--return-code 124" in commands[1]
    assert "--supervisor harbor_adapter_timeout" in commands[1]
    assert "--termination-kind TIMEOUT" in commands[1]


@pytest.mark.asyncio
async def test_harbor_adapter_installs_exact_scaffold_and_smoke_checks_graph_indexer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    indexer = tmp_path / "gt-index"
    indexer.write_bytes(b"source-built-indexer")
    dense = tmp_path / "dense"
    dense.mkdir()
    for name in ("model.onnx", "tokenizer.json", "manifest.json"):
        (dense / name).write_bytes(name.encode())
    monkeypatch.setenv("GT_DENSE_MODEL_DIR", str(dense))
    monkeypatch.setattr(
        GtHarnessMiniSwe246Agent,
        "_indexer_host_path",
        staticmethod(lambda: indexer),
    )
    uploads: list[tuple[str, str]] = []
    commands: list[str] = []

    class Environment:
        async def upload_dir(self, source, destination):
            uploads.append((str(source), destination))

        async def upload_file(self, source, destination):
            uploads.append((str(source), destination))

    async def capture_exec(environment, command, **kwargs):
        commands.append(command)

    agent = GtHarnessMiniSwe246Agent(logs_dir=tmp_path / "logs")
    monkeypatch.setattr(agent, "exec_as_root", capture_exec)
    monkeypatch.setattr(agent, "exec_as_agent", capture_exec)

    await agent.install(Environment())

    install = commands[-1]
    assert '--with "mini-swe-agent==2.4.6"' in install
    assert '--with "onnxruntime==1.28.0"' in install
    assert '--with "tokenizers==0.23.1"' in install
    assert "m.version('mini-swe-agent') == '2.4.6'" in install
    assert "mini-swe-agent==2.3" not in install
    assert "/installed-agent/gt-index -root /installed-agent/gt-harness/gt_engine" in install
    assert "test -s /tmp/gt-harbor-install-smoke.db" in install
    assert any(destination == "/installed-agent/gt-index" for _, destination in uploads)
    assert any(
        destination == "/installed-agent/snowflake-arctic-embed-m"
        for _, destination in uploads
    )


@pytest.mark.asyncio
async def test_harbor_adapter_creates_remote_source_parent_before_upload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Pier's Docker uploader does not create a missing destination parent."""

    indexer = tmp_path / "gt-index"
    indexer.write_bytes(b"source-built-indexer")
    dense = tmp_path / "dense"
    dense.mkdir()
    for name in ("model.onnx", "tokenizer.json"):
        (dense / name).write_bytes(name.encode())
    monkeypatch.setenv("GT_DENSE_MODEL_DIR", str(dense))
    monkeypatch.setattr(
        GtHarnessMiniSwe246Agent,
        "_indexer_host_path",
        staticmethod(lambda: indexer),
    )
    remote_source_exists = False
    remote_src_exists = False

    class Environment:
        async def upload_dir(self, source, destination):
            if destination.startswith("/installed-agent/gt-harness/"):
                assert remote_source_exists, (
                    "adapter uploaded into /installed-agent/gt-harness before "
                    "creating that parent directory"
                )
            if destination == "/installed-agent/gt-harness/src/groundtruth":
                assert remote_src_exists, (
                    "adapter uploaded groundtruth into a missing src parent"
                )

        async def upload_file(self, source, destination):
            if destination.startswith("/installed-agent/gt-harness/"):
                assert remote_source_exists

    async def capture_root_exec(environment, command, **kwargs):
        nonlocal remote_source_exists, remote_src_exists
        if "mkdir" in command and "/installed-agent/gt-harness" in command:
            remote_source_exists = True
        if "mkdir" in command and "/installed-agent/gt-harness/src" in command:
            remote_src_exists = True

    async def capture_agent_exec(environment, command, **kwargs):
        return None

    agent = GtHarnessMiniSwe246Agent(logs_dir=tmp_path / "logs")
    monkeypatch.setattr(agent, "exec_as_root", capture_root_exec)
    monkeypatch.setattr(agent, "exec_as_agent", capture_agent_exec)

    await agent.install(Environment())
    assert remote_source_exists
    assert remote_src_exists


def test_canonical_workflow_is_the_exact_one_attempt_repair20_product_path() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "max-parallel: 4" in workflow
    assert "provider_gate:" in workflow
    assert "gt-harness-provider-gate-${{ github.run_id }}" in workflow
    assert "needs: [plan, provider_gate]" in workflow
    assert "mini-swe-agent==2.4.6" in workflow
    assert "stealth/ox-alpha" in workflow
    assert "secrets.OPENROUTER_NEW" in workflow
    assert "eval.harbor_gt_harness_adapter:GtHarnessMiniSwe246Agent" in workflow
    assert '--ak task_id="$TASK"' in workflow
    assert '--ak product_source_sha="${{ needs.plan.outputs.source_sha }}"' in workflow
    assert "GT_RETRIEVAL_MODE" in workflow
    assert "hybrid_required" in workflow
    assert "from scripts.resolve_harbor_budget import (" in workflow
    assert "resolve_budget," in workflow
    assert '"time_budget_seconds": max(' in workflow
    assert "SUPERVISOR_GRACE_SECONDS" in workflow
    assert '- float(SUPERVISOR_GRACE_SECONDS)' in workflow
    assert '--ak time_budget_seconds="${{ matrix.time_budget_seconds }}"' in workflow
    assert "--ak time_budget_seconds=720" not in workflow
    assert "musl-tools" in workflow
    assert "CC: musl-gcc" in workflow
    assert '-extldflags "-static"' in workflow
    assert "statically linked" in workflow
    assert "-n 1" in workflow
    assert "-l 1" in workflow
    assert REPAIR20_SHA256 in workflow
    assert "source_sha: ${{ steps.source.outputs.sha }}" in workflow
    assert 'echo "sha=$(git rev-parse HEAD)" >> "$GITHUB_OUTPUT"' in workflow
    assert "SOURCE_SHA: ${{ needs.plan.outputs.source_sha }}" in workflow
    assert "SOURCE_SHA: ${{ github.sha }}" not in workflow
    assert "nonterminal_product_receipt" in workflow
    assert "product_error:" in workflow
    assert "missing_full_trajectory" in workflow
    assert "provider_call_receipt_mismatch" in workflow
    assert 'trajectory.get("info")' in workflow
    assert 'model_stats.get("api_calls")' in workflow
    assert "gt_treatment_unavailable" in workflow
    assert "active_graph_not_ready" in workflow
    assert "active_dense_not_ready" in workflow
    task_block = workflow.split("          tasks = [", 1)[1].split("          ]", 1)[0]
    observed_tasks = tuple(re.findall(r'^\s+"([^"]+)",$', task_block, re.MULTILINE))
    assert observed_tasks == REPAIR20_TASKS
    assert (
        hashlib.sha256(
            ("\n".join(sorted(observed_tasks)) + "\n").encode("utf-8")
        ).hexdigest()
        == REPAIR20_SHA256
    )
    assert 'canonical = "\\n".join(sorted(tasks)) + "\\n"' in workflow
    assert 'hashlib.sha256(canonical.encode("utf-8"))' in workflow
    assert workflow.count("OPENROUTER_NEW") == 2
    assert "${{ secrets.OPENROUTER_NEW }}" in workflow
    assert "provider_secret" not in workflow
    for forbidden_trigger in ("push:", "pull_request:", "schedule:"):
        assert f"\n  {forbidden_trigger}" not in workflow

    lowered = workflow.lower()
    for forbidden in (
        "eval.gt_central_agent",
        "nano",
        "mini-swe-agent==2.3",
        " mcp ",
        "benchmark-only bridge",
    ):
        assert forbidden not in lowered


def test_tb2_attestation_uses_role_phase_delivery_budgets() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert 'token_limit = 500 if delivery.get("kind") == "repository_start" else 350' in workflow
    assert 'context_token_count") or 0) > token_limit' in workflow
    assert "total_context_budget_exceeded" in workflow
    assert 'context_token_count") or 0) > 350' not in workflow


def test_cleanup_ledger_preserves_evidence_and_classifies_legacy_paths() -> None:
    ledger = CLEANUP_LEDGER.read_text(encoding="utf-8")

    assert ".github/workflows/tb2_miniswe_product.yml" in ledger
    assert "RETIRED_FROM_DISPATCH" in ledger
    assert "eval/gt_central_agent.py" in ledger
    assert "eval/miniswe_agent.py" in ledger
    assert "eval/pier_gt_adapter.py" in ledger
    assert "scripts/miniswe_gt_run.py" in ledger
    assert "artifacts/" in ledger
    assert "excluded from the wheel" in ledger.lower()

    for retired in (
        ".github/workflows/tb2_miniswe_ox_alpha_diagnostic.yml",
        ".github/workflows/tb2_miniswe_central.yml",
        ".github/workflows/tb2_miniswe_engine.yml",
        ".github/workflows/deepswe_miniswe_central.yml",
    ):
        assert not (ROOT / retired).exists(), retired

    for retained in (
        "eval/gt_central_agent.py",
        "eval/miniswe_agent.py",
        "eval/pier_gt_adapter.py",
        "scripts/miniswe_gt_run.py",
    ):
        assert (ROOT / retained).exists(), retained


def test_canonical_workflow_embedded_python_is_syntactically_valid() -> None:
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    parsed = 0
    marker = "python - <<'PY'"
    for job in document["jobs"].values():
        for step in job.get("steps", []):
            script = step.get("run", "")
            if marker not in script:
                continue
            source = script.split(marker, 1)[1].rsplit("\nPY", 1)[0].lstrip("\n")
            ast.parse(source)
            parsed += 1
    assert parsed == 3
