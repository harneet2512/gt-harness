from __future__ import annotations

import base64
import hashlib
import inspect
import io
import json
import os
import tarfile
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from harbor.agents.base import BaseAgent
from harbor.agents.installed.base import BaseInstalledAgent
from harbor.environments.base import ExecResult
from harbor.models.agent.context import AgentContext
from minisweagent.exceptions import FormatError

from eval.gt_central_agent import (
    GTIntegrationMode,
    MiniSweCentralAgent,
    MiniSweCentralShadowAgent,
    _bootstrap_provider_call_kwargs,
    _graph_gate_degraded_fallback,
    _graph_transition_paths,
    _message_context_chars,
    _partition_recovered_repository_failures,
    _preemptive_lifecycle_budget,
    _provider_error_receipt,
    _provider_request_receipt,
    _provider_response_identity,
    _provider_response_summary,
    _provider_route_configuration,
    _provider_visible_claim_ids,
    _resolved_repository_evidence,
    _retrieval_intent,
    _stable_provider_prefix,
    _task_prompt_with_workspace,
    _workspace_target_path,
)
from gt_engine.benchmark_parity import (
    RUNTIME_FIELD_ORIGINS,
    RuntimeFieldObservation,
    build_runtime_execution_observation,
    runtime_observation_hash,
)
from gt_engine.central_runtime import (
    FileState,
    WorkspaceSnapshot,
    WorkspaceTransition,
    classify_change,
    classify_validation_command,
)
from gt_engine.decision_point_eval import (
    DecisionPointValidity,
    validate_decision_point_row,
)
from gt_engine.delivery_audit import audit_provider_deliveries
from gt_engine.hybrid_repository import HybridRepository
from gt_engine.hybrid_retrieval import (
    RepositoryDocument,
    RetrievalCandidate,
    RetrievalChannel,
    RetrievalIntent,
)
from gt_engine.indexer import IndexBuildReceipt, IndexBuildStatus
from gt_engine.persistent_execution_state import (
    bootstrap_visible_item_ids,
    build_bootstrap_catalog,
    build_bootstrap_messages,
)
from gt_engine.preflight import (
    ActionDisposition,
    ActionOperation,
    MutationCertainty,
    PreflightDecision,
    PreflightMode,
    adapt_proposed_action,
)
from gt_engine.replay_bundle import load_replay_bundle
from gt_engine.repository_intelligence import RepositoryEvidence, RepositorySession
from gt_engine.repository_mirror import SourceMirrorPlan
from gt_engine.snowflake_onnx import (
    SNOWFLAKE_MAX_LENGTH,
    SNOWFLAKE_MODEL_NAME,
    SNOWFLAKE_MODEL_REVISION,
    SNOWFLAKE_MODEL_SHA256,
    SNOWFLAKE_TOKENIZER_SHA256,
)
from gt_engine.uplift_policy import GTPolicyMode
from scripts.central_bootstrap_canary import production_shaped_catalog, validate_canary
from scripts.central_release_gate import audit_treatment_runtime


def _runtime_contract_fixture() -> dict[str, object]:
    return {
        "task_count": 2,
        "task_order_sha256": hashlib.sha256(b"order").hexdigest(),
        "provider_identity": "fixture/provider",
        "temperature": 0.0,
        "sampling_parameters": {"top_p": 1.0},
        "tool_envelope_sha256": hashlib.sha256(b"tools").hexdigest(),
        "hook_envelope_sha256": hashlib.sha256(b"hooks").hexdigest(),
        "embedding_configuration_sha256": hashlib.sha256(b"embedding").hexdigest(),
        "hardware_assumptions_sha256": hashlib.sha256(b"hardware").hexdigest(),
        "retry_policy_sha256": hashlib.sha256(b"retry").hexdigest(),
        "timeout_policy_sha256": hashlib.sha256(b"timeout").hexdigest(),
        "token_accounting_sha256": hashlib.sha256(b"tokens").hexdigest(),
    }


def _benchmark_identity_fixture(contract: dict[str, object]) -> dict[str, object]:
    return {
        "model_id": "fixture/provider",
        "max_steps": 19,
        "execution_contract": contract,
        "treatment": {"treatment_id": "groundtruth", "agent_kwargs": {}},
    }


def test_agent_does_not_promote_legacy_declared_contract_to_runtime_observation(
    tmp_path,
):
    contract = _runtime_contract_fixture()
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="fixture/provider",
        temperature=0.0,
        step_limit=19,
        benchmark_identity=_benchmark_identity_fixture(contract),
        observed_execution_contract=contract,
    )

    observed = agent._observed_benchmark_runtime_contract()

    assert observed is not None
    assert observed["schema"] == "gt.agent_runtime_observation.partial.v1"
    assert set(observed["execution_contract"]) == {"provider_identity", "temperature"}
    assert "task_count" in observed["unobserved_fields"]
    assert observed["field_sources"]["provider_identity"] == {
        "origin": "provider_request",
        "value_sha256": runtime_observation_hash("fixture/provider"),
    }


def test_agent_merges_actual_provider_fields_into_sourced_runner_observation(tmp_path):
    contract = _runtime_contract_fixture()
    runner_observation = build_runtime_execution_observation(
        {
            field: RuntimeFieldObservation(contract[field], sorted(origins)[0])
            for field, origins in RUNTIME_FIELD_ORIGINS.items()
        }
    )
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="fixture/provider",
        temperature=0.0,
        step_limit=19,
        benchmark_identity=_benchmark_identity_fixture(contract),
        runtime_observation=runner_observation,
    )

    observed = agent._observed_benchmark_runtime_contract()

    assert observed is not None
    assert observed["schema"] == "gt.benchmark_runtime_observation.v1"
    assert observed["execution_contract"] == contract
    assert observed["unobserved_fields"] == []
    assert observed["field_sources"]["temperature"] == {
        "origin": "agent_instance",
        "value_sha256": runtime_observation_hash(0.0),
    }


def test_agent_loads_runner_observation_from_host_path(tmp_path):
    contract = _runtime_contract_fixture()
    runner_observation = build_runtime_execution_observation(
        {
            field: RuntimeFieldObservation(contract[field], sorted(origins)[0])
            for field, origins in RUNTIME_FIELD_ORIGINS.items()
        }
    )
    observation_path = tmp_path / "runtime-observation.json"
    observation_path.write_text(json.dumps(runner_observation), encoding="utf-8")

    agent = MiniSweCentralAgent(
        logs_dir=tmp_path / "logs",
        model_name="fixture/provider",
        temperature=0.0,
        step_limit=19,
        benchmark_identity=_benchmark_identity_fixture(contract),
        runtime_observation_path=observation_path,
    )

    observed = agent._observed_benchmark_runtime_contract()

    assert observed is not None
    assert observed["schema"] == "gt.benchmark_runtime_observation.v1"
    assert observed["execution_contract"] == contract


def test_agent_loads_runner_observation_from_environment_path(tmp_path, monkeypatch):
    contract = _runtime_contract_fixture()
    runner_observation = build_runtime_execution_observation(
        {
            field: RuntimeFieldObservation(contract[field], sorted(origins)[0])
            for field, origins in RUNTIME_FIELD_ORIGINS.items()
        }
    )
    observation_path = tmp_path / "runtime-observation.json"
    observation_path.write_text(json.dumps(runner_observation), encoding="utf-8")
    monkeypatch.setenv("GT_RUNTIME_OBSERVATION_PATH", str(observation_path))

    agent = MiniSweCentralAgent(
        logs_dir=tmp_path / "logs",
        model_name="fixture/provider",
        temperature=0.0,
        step_limit=19,
        benchmark_identity=_benchmark_identity_fixture(contract),
    )

    observed = agent._observed_benchmark_runtime_contract()

    assert observed is not None
    assert observed["execution_contract"] == contract


def test_explicit_runtime_observation_wins_over_environment_conflict(tmp_path, monkeypatch):
    contract = _runtime_contract_fixture()
    explicit_observation = build_runtime_execution_observation(
        {
            field: RuntimeFieldObservation(contract[field], sorted(origins)[0])
            for field, origins in RUNTIME_FIELD_ORIGINS.items()
        }
    )
    observation_path = tmp_path / "runtime-observation.json"
    observation_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GT_RUNTIME_OBSERVATION_PATH", str(observation_path))

    agent = MiniSweCentralAgent(
        logs_dir=tmp_path / "logs",
        model_name="fixture/provider",
        temperature=0.0,
        benchmark_identity=_benchmark_identity_fixture(contract),
        runtime_observation=explicit_observation,
    )

    assert agent._observed_benchmark_runtime_contract()["execution_contract"] == contract


def test_agent_rejects_two_runtime_observation_sources(tmp_path):
    observation_path = tmp_path / "runtime-observation.json"
    observation_path.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="one runtime observation source"):
        MiniSweCentralAgent(
            logs_dir=tmp_path / "logs",
            model_name="fixture/provider",
            runtime_observation={},
            runtime_observation_path=observation_path,
        )


def test_preemptive_lifecycle_budget_preserves_late_failure_capacity():
    assert _preemptive_lifecycle_budget("task_start", task_budget_chars=12_000) == (800, 1)
    assert _preemptive_lifecycle_budget("post_read_search", task_budget_chars=12_000) == (2_400, 1)
    assert _preemptive_lifecycle_budget("post_mutation", task_budget_chars=12_000) == (
        3_600,
        2,
    )
    assert _preemptive_lifecycle_budget("post_diagnostic", task_budget_chars=12_000) == (5_200, 2)
    assert _preemptive_lifecycle_budget("post_other", task_budget_chars=12_000) == (0, 0)


def test_recovered_frontier_failure_is_receipted_but_not_current_failure():
    current, transient = _partition_recovered_repository_failures(
        [
            {
                "source_revision": "r1",
                "disposition": "substrate_failure",
            },
            {
                "source_revision": "r1",
                "disposition": "no_frontier",
            },
        ],
        current_source_revision="r1",
        failure_values=frozenset({"substrate_failure", "stale_source_revision"}),
        prefix="frontier",
    )

    assert current == []
    assert transient == ["frontier:substrate_failure"]


def test_current_frontier_failure_remains_fail_closed():
    current, transient = _partition_recovered_repository_failures(
        [
            {
                "source_revision": "r1",
                "disposition": "substrate_failure",
            }
        ],
        current_source_revision="r1",
        failure_values=frozenset({"substrate_failure", "stale_source_revision"}),
        prefix="frontier",
    )

    assert current == ["frontier:substrate_failure"]
    assert transient == []


@pytest.mark.parametrize(
    ("path", "cwd", "expected"),
    [
        ("/workspace/src/main.py", "/workspace", "src/main.py"),
        ("/app/dclm/ray/process.py", "/app/dclm", "ray/process.py"),
        ("/etc/nginx/nginx.conf", "/app", "/etc/nginx/nginx.conf"),
        ("./src/main.py", "/workspace", "src/main.py"),
    ],
)
def test_workspace_target_path_is_relative_to_resolved_cwd(path, cwd, expected):
    assert _workspace_target_path(path, cwd=cwd) == expected


def test_workspace_prompt_discloses_resolved_task_root_without_gt_advice():
    prompt = _task_prompt_with_workspace("Please solve the task.\n", cwd="/app")

    assert prompt.startswith("Please solve the task.")
    assert "The repository workspace for this task is /app." in prompt
    assert "Each Bash action starts in that directory" in prompt
    assert "GT" not in prompt


def test_provider_visibility_accounts_for_normal_miniswe_read_output():
    candidate = RetrievalCandidate(
        path="src/greeter.py",
        start_line=1,
        end_line=2,
        symbol="greet",
        text="def greet(name):\n    return f'hello {name}'",
        channel=RetrievalChannel.EXACT,
        channel_rank=1,
        relation=None,
        provenance=("exact_path",),
        source_revision="graph-1",
    )

    visible = _provider_visible_claim_ids(
        [{"role": "tool", "content": "file output:\n" + candidate.text}],
        (candidate,),
    )

    assert visible == (candidate.claim_hash,)


def test_recovered_initial_graph_failure_does_not_remain_degraded():
    """A transient pre-source snapshot must not invalidate a later fresh graph."""

    assert (
        _graph_gate_degraded_fallback(
            initial_failures=("no_supported_source", "graph_missing"),
            current_failures=(),
        )
        is False
    )
    assert (
        _graph_gate_degraded_fallback(
            initial_failures=("no_supported_source",),
            current_failures=("graph_not_current",),
        )
        is True
    )


def test_final_repository_evidence_uses_recovered_session_state():
    stale = RepositoryEvidence(available=False, status="refresh_timeout")
    recovered = RepositoryEvidence(
        available=True,
        status="source_backed",
        source_revision="graph-2",
        index_current=True,
        intelligence_valid=True,
        substrate_ready=True,
    )
    session = type("Session", (), {"evidence": recovered})()

    assert _resolved_repository_evidence(stale, session) is recovered
    assert _resolved_repository_evidence(stale, None) is stale


def test_merge_gate_does_not_promote_recovered_transient_repository_failures():
    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "tb2_miniswe_central.yml"
    ).read_text(encoding="utf-8")
    merge_block = workflow[workflow.index("invalid_intelligence = [") : workflow.index("out = [")]
    assert "repository_intelligence_transient_failures" not in merge_block


def test_deepswe_registered_entrypoint_routes_to_the_product_workflow():
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "deepswe_miniswe_central.yml"
    ).read_text(encoding="utf-8")

    assert "uses: ./.github/workflows/deepswe_gt_harness_product.yml" in workflow
    assert "secrets: inherit" in workflow
    assert "eval.pier_gt_adapter" not in workflow
    assert "eval.gt_central_agent" not in workflow


def test_deepswe_product_workflow_uses_ox_alpha_and_pins_v11_catalog_snapshot():
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "deepswe_gt_harness_product.yml"
    ).read_text(encoding="utf-8")

    assert "stealth/ox-alpha" in workflow
    assert workflow.count("ref: 435ee89ec2f2e2289f33b0da4f992f0b7b7266b9") == 2
    assert "v1.0.0" not in workflow
    assert "eval/deepswe_smoke20_v1.json" in workflow
    assert "mimo-v2.5-pro" not in workflow


def test_deepswe_product_workflow_uses_pier_v11_verifier_protocol():
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "deepswe_gt_harness_product.yml"
    ).read_text(encoding="utf-8")

    # Matching task IDs is not sufficient for DeepSWE v1.1.  Its separate
    # verifier/collect-patch lifecycle is implemented by the pinned Pier
    # runner, not by a direct Harbor invocation.
    assert '"datacurve-pier==0.3.1"' in workflow
    assert "pier run \\" in workflow
    assert "-p deepswe-bench/tasks \\" in workflow
    assert '--include-task-name "$TASK"' in workflow
    assert "eval.pier_gt_harness_adapter:PierGtHarnessMiniSwe228Agent" in workflow
    assert "gt-harness run" in workflow
    assert "harbor run -p deepswe-bench/tasks" not in workflow


def test_deepswe_product_workflow_gates_the_exact_openrouter_route():
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "deepswe_gt_harness_product.yml"
    ).read_text(encoding="utf-8")

    assert "provider_gate:" in workflow
    assert "https://openrouter.ai/api/v1/chat/completions" in workflow
    assert "OPENAI_BASE_URL: https://openrouter.ai/api/v1" in workflow
    assert workflow.count("secrets.OPENROUTER_NEW") == 2
    assert '"model":"stealth/ox-alpha"' in workflow
    assert "TOKENROUTER" not in workflow
    assert "DEEPSEEK_API_KEY" not in workflow


def test_openrouter_model_builder_pins_exact_model_and_provider(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("GT_OPENROUTER_PROVIDER_ONLY", "deepseek")
    monkeypatch.setenv("GT_OPENROUTER_DATA_COLLECTION", "allow")
    monkeypatch.setenv("GT_LITELLM_MODEL", "openai/deepseek/deepseek-v4-flash-0731")

    model = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="deepseek-v4-flash-0731",
    )._build_model()

    assert model.config.model_name == "openai/deepseek/deepseek-v4-flash-0731"
    assert model.config.model_kwargs["api_base"] == "https://openrouter.ai/api/v1"
    assert "api_key" not in model.config.model_kwargs
    assert model.config.model_kwargs["extra_body"] == {
        "provider": {
            "only": ["deepseek"],
            "order": ["deepseek"],
            "allow_fallbacks": False,
            "require_parameters": True,
            "data_collection": "allow",
        }
    }
    route = _provider_route_configuration(model)
    assert route["credential_in_receipt"] is False
    assert route["retry_policy"] == "provider_once_no_retry"
    assert route["provider_policy"]["only"] == ["deepseek"]
    assert route["provider_policy"]["data_collection"] == "allow"


def test_openrouter_model_builder_does_not_override_unset_data_policy(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("GT_OPENROUTER_PROVIDER_ONLY", "deepseek")
    monkeypatch.delenv("GT_OPENROUTER_DATA_COLLECTION", raising=False)
    monkeypatch.setenv("GT_LITELLM_MODEL", "openai/deepseek/deepseek-v4-flash-0731")

    model = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="deepseek-v4-flash-0731",
    )._build_model()

    assert "data_collection" not in model.config.model_kwargs["extra_body"]["provider"]


def test_openai_compatible_route_preserves_exact_deepseek_checkpoint(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.tokenrouter.com/v1")
    monkeypatch.delenv("GT_OPENROUTER_PROVIDER_ONLY", raising=False)
    monkeypatch.setenv("GT_LITELLM_MODEL", "openai/deepseek/deepseek-v4-flash-0731")

    model = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="deepseek-v4-flash-0731",
    )._build_model()

    assert model.config.model_name == "openai/deepseek/deepseek-v4-flash-0731"
    assert model.config.model_kwargs["api_base"] == "https://api.tokenrouter.com/v1"
    assert "extra_body" not in model.config.model_kwargs
    route = _provider_route_configuration(model)
    assert route["thinking_mode"] == ""


def test_native_deepseek_route_uses_explicit_litellm_model(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("GT_LITELLM_MODEL", "openai/deepseek-v4-flash")
    monkeypatch.setenv(
        "GT_PROVIDER_ROUTE_ID", "deepseek:native:api.deepseek.com"
    )
    monkeypatch.delenv("GT_OPENROUTER_PROVIDER_ONLY", raising=False)

    model = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="deepseek-v4-flash",
    )._build_model()

    assert model.config.model_name == "openai/deepseek-v4-flash"
    assert model.config.model_kwargs["api_base"] == "https://api.deepseek.com"
    assert "extra_body" not in model.config.model_kwargs
    route = _provider_route_configuration(model)
    assert route["route_id"] == "deepseek:native:api.deepseek.com"
    assert route["api_host"] == "api.deepseek.com"


def test_provider_response_identity_records_actual_model_route_without_secrets():
    row = _provider_response_identity(
        {
            "role": "assistant",
            "extra": {
                "response": {
                    "model": "deepseek/deepseek-v4-flash-0731",
                    "provider": "deepseek",
                    "system_fingerprint": "fp-current",
                    "api_key": "must-not-escape",
                }
            },
        }
    )

    assert row == {
        "model": "deepseek/deepseek-v4-flash-0731",
        "provider": "deepseek",
        "system_fingerprint": "fp-current",
    }
    assert "must-not-escape" not in json.dumps(row)


def test_provider_error_receipt_is_diagnostic_without_exposing_message():
    class ProviderFailure(Exception):
        status_code = 400
        code = "invalid_request"
        message = "request rejected with secret-token"

    row = _provider_error_receipt(ProviderFailure(ProviderFailure.message))

    assert row["type"] == "ProviderFailure"
    assert row["status_code"] == 400
    assert row["code"] == "invalid_request"
    assert row["retryable"] is False
    assert len(row["message_sha256"]) == 64
    assert "secret-token" not in json.dumps(row)


def test_provider_request_hash_covers_effective_call_arguments_not_only_messages():
    model = _ScriptedModel(["echo unused"])
    messages = [{"role": "user", "content": "same provider-visible message"}]

    _, temp_zero_hash, temp_zero_messages_hash, _ = _provider_request_receipt(
        model,
        messages,
        call_kwargs={"temperature": 0.0, "max_tokens": 512, "timeout": 5.0},
    )
    _, temp_one_hash, temp_one_messages_hash, _ = _provider_request_receipt(
        model,
        messages,
        call_kwargs={"temperature": 1.0, "max_tokens": 1024, "timeout": 10.0},
    )

    assert temp_zero_hash != temp_one_hash
    assert temp_zero_messages_hash == temp_one_messages_hash


def test_provider_response_summary_fails_closed_on_mixed_or_missing_identity():
    summary = _provider_response_summary(
        (
            {
                "model": "deepseek/deepseek-v4-flash-0731",
                "provider": "deepseek",
                "system_fingerprint": "fp-a",
            },
            {
                "model": "fallback-model",
                "provider": "other",
                "system_fingerprint": "",
            },
        )
    )

    assert summary["response_count"] == 2
    assert summary["model_identity_complete"] is True
    assert summary["fingerprint_identity_complete"] is False
    assert summary["stable_model_identity"] is False
    assert summary["stable_provider_identity"] is False
    assert summary["stable_fingerprint_identity"] is False


def test_deepswe_final_workflow_is_commit_provider_outcome_and_timeout_exact():
    entrypoint = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "deepswe_miniswe_central.yml"
    ).read_text(encoding="utf-8")
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "deepswe_gt_harness_product.yml"
    ).read_text(encoding="utf-8")

    assert "uses: ./.github/workflows/deepswe_gt_harness_product.yml" in entrypoint
    assert "secrets: inherit" in entrypoint
    assert "eval.gt_central_agent" not in entrypoint
    assert "workflow_call:" in workflow
    assert 'echo "sha=$(git rev-parse HEAD)" >> "$GITHUB_OUTPUT"' in workflow
    assert "eval/deepswe_smoke20_v1.json" in workflow
    assert '"agent_scaffold_version": "2.2.8"' in workflow
    assert '"max_parallel": 4' in workflow
    assert "max-parallel: 4" in workflow
    assert "stealth/ox-alpha" in workflow
    assert "OPENAI_BASE_URL: https://openrouter.ai/api/v1" in workflow
    assert "--ak max_iterations=300" in workflow
    assert "SUPERVISOR_GRACE_SECONDS" in workflow
    assert "gt-run.json" in workflow
    assert "gt-run.trajectory.json" in workflow
    assert "benchmark-adapter.json" in workflow
    assert '"solved": reward == 1 or reward == 1.0' in workflow
    assert '"schema": "gt.deepswe_gt_harness_attestation.v1"' in workflow
    assert "eval.gt_central_agent" not in workflow
    assert "persistent_state_only" not in workflow
    assert "deepseek-v4-flash" not in workflow


def test_provider_free_workflow_covers_final_hardening_and_exact_commit():
    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "central_provider_free.yml"
    ).read_text(encoding="utf-8")

    assert "tests/test_diagnostics.py" in workflow
    assert "tests/test_deepswe_release_gate.py" in workflow
    assert "gt_engine/diagnostics.py" in workflow
    assert "scripts/deepswe_release_gate.py" in workflow
    assert '["git", "rev-parse", "HEAD"]' in workflow
    assert "ref: ${{ inputs.ref || github.sha }}" in workflow
    assert "workflow_call:" in workflow
    assert "'.[dev,retrieval]'" in workflow
    assert '"dispatch_sha": "${{ github.sha }}"' in workflow
    assert '"provider_calls": 0' in workflow


def test_pier_adapter_isolated_from_runner_neutral_central_agent():
    root = Path(__file__).resolve().parents[1]
    source = (root / "eval" / "pier_gt_adapter.py").read_text(encoding="utf-8")
    central = (root / "eval" / "gt_central_agent.py").read_text(encoding="utf-8")

    assert "class PierMiniSweCentralAgent" in source
    assert "from pier." in source
    assert "from pier." not in central
    assert "class MiniSweCentralAgent" in central


def test_initial_index_timeout_is_configurable_and_clamped(tmp_path):
    defaulted = MiniSweCentralAgent(
        logs_dir=tmp_path / "defaulted",
        model_name="test",
        repository_initial_index_timeout_sec=0.5,
    )
    configured = MiniSweCentralAgent(
        logs_dir=tmp_path / "configured",
        model_name="test",
        repository_initial_index_timeout_sec=42.5,
    )

    assert defaulted.repository_initial_index_timeout_sec == 1.0
    assert configured.repository_initial_index_timeout_sec == 42.5


def test_incremental_refresh_timeout_is_configurable_and_not_shorter_than_indexer(tmp_path):
    defaulted = MiniSweCentralAgent(
        logs_dir=tmp_path / "defaulted-refresh",
        model_name="test",
    )
    configured = MiniSweCentralAgent(
        logs_dir=tmp_path / "configured-refresh",
        model_name="test",
        repository_refresh_timeout_sec=60,
    )

    assert defaulted.repository_refresh_timeout_sec >= 35.0
    assert configured.repository_refresh_timeout_sec == 60.0


def test_recovered_refresh_failure_is_not_current_failure():
    current, transient = _partition_recovered_repository_failures(
        [
            {"source_revision": "r1", "status": "sensor_degraded"},
            {"source_revision": "r1", "status": "source_backed"},
        ],
        current_source_revision="r1",
        failure_values=frozenset({"sensor_degraded", "index_unavailable"}),
        prefix="repository_refresh",
    )

    assert current == []
    assert transient == ["repository_refresh:sensor_degraded"]


class _Environment:
    default_user = "root"

    def __init__(self):
        self.commands: list[tuple[str, dict | None]] = []

    async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
        self.commands.append((command, env))
        if command == "pwd -P":
            return ExecResult(stdout="/app\n", return_code=0)
        if command.startswith("uname "):
            return ExecResult(stdout="Linux\t6.8\tversion\tx86_64\n", return_code=0)
        if "-printf" in command:
            return ExecResult(stdout="", return_code=0)
        return ExecResult(stdout="", return_code=0)


@pytest.mark.asyncio
async def test_system_information_is_invariant_to_host_kernel_release(tmp_path):
    class KernelEnvironment(_Environment):
        def __init__(self, release: str, version: str):
            super().__init__()
            self.release = release
            self.version = version

        async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
            self.commands.append((command, env))
            if "/etc/os-release" in command:
                return ExecResult(
                    stdout="Linux\nUbuntu\n22.04\nx86_64\n/app\n",
                    return_code=0,
                )
            if command.startswith("uname "):
                return ExecResult(
                    stdout=(f"Linux\n{self.release}\n{self.version}\nx86_64\n/app\n"),
                    return_code=0,
                )
            return await super().exec(command, cwd, env, timeout_sec, user)

    first = MiniSweCentralAgent(logs_dir=tmp_path / "first", model_name="test")
    second = MiniSweCentralAgent(logs_dir=tmp_path / "second", model_name="test")

    first_info = await first._system_information(KernelEnvironment("6.17.0-1020-azure", "runner-a"))
    second_info = await second._system_information(
        KernelEnvironment("6.17.0-1022-azure", "runner-b")
    )

    assert first_info == second_info
    assert first_info == {
        "system": "Linux",
        "release": "Ubuntu",
        "version": "22.04",
        "machine": "x86_64",
    }


def test_passing_validation_does_not_request_repository_context():
    assert (
        _retrieval_intent(
            operation=ActionOperation.VALIDATE.value,
            validation_state="pass",
            changed_paths=(),
            diagnostics=(),
        )
        is RetrievalIntent.OTHER
    )


def test_persistent_state_is_one_switch_and_off_audit_cannot_enable_it(tmp_path):
    active = MiniSweCentralAgent(
        logs_dir=tmp_path / "active",
        model_name="test",
        integration_mode="active",
        enable_persistent_execution_state=True,
    )
    off = MiniSweCentralAgent(
        logs_dir=tmp_path / "off",
        model_name="test",
        integration_mode="off",
        enable_persistent_execution_state=True,
    )
    audit = MiniSweCentralAgent(
        logs_dir=tmp_path / "audit",
        model_name="test",
        integration_mode="audit",
        enable_persistent_execution_state=True,
    )

    assert active.enable_persistent_execution_state is True
    assert off.enable_persistent_execution_state is False
    assert audit.enable_persistent_execution_state is False


def test_relational_v2_profile_strengthens_persistent_state_without_replacing_it(tmp_path):
    active = MiniSweCentralAgent(
        logs_dir=tmp_path / "active-relational",
        model_name="test",
        integration_mode="active",
        treatment_profile="central_relational_v2",
        enable_persistent_execution_state=True,
        enable_preemptive_retrieval=False,
        relational_context_max_depth=4,
        relational_context_max_branching=2,
        relational_context_max_processes=2,
        relational_context_max_tokens=144,
    )
    off = MiniSweCentralAgent(
        logs_dir=tmp_path / "off-relational",
        model_name="test",
        integration_mode="off",
        treatment_profile="central_relational_v2",
    )

    assert active.treatment_profile == "central_relational_v2"
    assert active.enable_persistent_execution_state is True
    assert active.enable_preemptive_retrieval is True
    assert active.persistent_state_selection_mode == "deterministic_v1"
    assert active.retrieval_delivery_mode == "integrated_same_observation"
    assert active.enable_relational_context is True
    assert active.enable_semantic_evidence is True
    assert active.dense_fallback_only is True
    assert active.relational_context_max_depth == 4
    assert active.relational_context_max_branching == 2
    assert active.relational_context_max_processes == 2
    assert active.relational_context_max_tokens == 144
    assert off.enable_preemptive_retrieval is False
    assert off.retrieval_delivery_mode == "disabled"
    assert off.enable_relational_context is False
    assert off.enable_semantic_evidence is False


def test_semantic_evidence_bridge_is_explicit_and_isolated(tmp_path):
    active = MiniSweCentralAgent(
        logs_dir=tmp_path / "semantic-active",
        model_name="test",
        integration_mode="active",
        treatment_profile="central_relational_v2",
        enable_semantic_evidence=True,
        semantic_evidence_max_items=4,
        semantic_evidence_max_tokens=128,
    )
    audit = MiniSweCentralAgent(
        logs_dir=tmp_path / "semantic-audit",
        model_name="test",
        integration_mode="audit",
        enable_semantic_evidence=True,
    )

    assert active.enable_semantic_evidence is True
    assert active.semantic_evidence_max_items == 4
    assert active.semantic_evidence_max_tokens == 128
    assert audit.enable_semantic_evidence is False


def test_legacy_profile_preserves_existing_switches_and_unknown_profile_fails(tmp_path):
    legacy = MiniSweCentralAgent(
        logs_dir=tmp_path / "legacy",
        model_name="test",
        treatment_profile="central_pes_v1",
        enable_persistent_execution_state=True,
        enable_preemptive_retrieval=False,
    )

    assert legacy.treatment_profile == "central_pes_v1"
    assert legacy.enable_persistent_execution_state is True
    assert legacy.enable_preemptive_retrieval is False
    assert legacy.enable_relational_context is False
    assert legacy.dense_fallback_only is False

    with pytest.raises(ValueError, match="unknown GT treatment profile"):
        MiniSweCentralAgent(
            logs_dir=tmp_path / "invalid",
            model_name="test",
            treatment_profile="invented-profile",
        )


def test_source_less_task_blocks_preemptive_repository_retrieval():
    from eval import gt_central_agent as central_agent

    gate = getattr(central_agent, "_preemptive_retrieval_gate_reason", None)
    assert callable(gate), "central agent must expose one applicability gate"
    assert (
        gate(
            enabled=True,
            integration_active=True,
            policy_active=True,
            treatment=True,
            source_less_task_at_start=True,
        )
        == "not_applicable_no_supported_source"
    )
    assert (
        gate(
            enabled=True,
            integration_active=True,
            policy_active=True,
            treatment=True,
            source_less_task_at_start=False,
        )
        is None
    )
    assert (
        gate(
            enabled=True,
            integration_active=True,
            policy_active=True,
            treatment=True,
            source_less_task_at_start=False,
            last_operation=ActionOperation.VALIDATE.value,
            validation_state="pass",
            diagnostics=(),
        )
        == "validation_pass_no_diagnostic"
    )


def test_persistent_bootstrap_owns_task_start_retrieval_delivery():
    from eval import gt_central_agent as central_agent

    gate = central_agent._preemptive_retrieval_gate_reason
    assert (
        gate(
            enabled=True,
            integration_active=True,
            policy_active=True,
            treatment=True,
            source_less_task_at_start=False,
            evidence_action=0,
            persistent_bootstrap_selected=True,
        )
        == "persistent_bootstrap_owns_task_start"
    )
    # After dynamic source creation, task-start remains PES-owned; later
    # action-conditioned retrieval may still run when evidence_action > 0.
    assert (
        gate(
            enabled=True,
            integration_active=True,
            policy_active=True,
            treatment=True,
            source_less_task_at_start=False,
            evidence_action=3,
            persistent_bootstrap_selected=True,
        )
        is None
    )
    assert (
        gate(
            enabled=True,
            integration_active=True,
            policy_active=True,
            treatment=True,
            source_less_task_at_start=False,
            evidence_action=1,
            persistent_bootstrap_selected=True,
        )
        is None
    )


def test_live_retrieval_action_state_uses_typed_parser_not_raw_program_body():
    from eval import gt_central_agent as central_agent

    proposed = adapt_proposed_action(
        {
            "command": "python - <<'PY'\nSECRET_PROGRAM_BODY = 'never query this'\nPY",
            "tool_call_id": "action-1",
        },
        source_revision="source-1",
        workspace_revision="workspace-1",
        model_call=1,
        batch_index=0,
        batch_size=1,
    )
    build_state = getattr(central_agent, "_retrieval_action_state", None)

    assert callable(build_state), "central agent must use the typed action adapter"
    action_state = build_state(proposed, target_paths=("src/worker.py",))
    assert action_state.operation == proposed.operation.value
    assert action_state.executable == "python"
    assert action_state.targets == ("src/worker.py",)
    assert "SECRET_PROGRAM_BODY" not in action_state.query_text()


def test_live_retrieval_action_state_uses_validator_segment_after_shell_context():
    from eval import gt_central_agent as central_agent

    proposed = adapt_proposed_action(
        {
            "command": "cd /app && go test ./...",
            "tool_call_id": "action-validate",
        },
        source_revision="source-1",
        workspace_revision="workspace-1",
        model_call=2,
        batch_index=0,
        batch_size=1,
    )

    action_state = central_agent._retrieval_action_state(
        proposed,
        target_paths=("pkg/worker.go",),
    )

    assert proposed.operation is ActionOperation.VALIDATE
    assert action_state.operation == "validate"
    assert action_state.executable == "go"
    assert action_state.validation_kind == "go"


@pytest.mark.asyncio
async def test_default_cwd_is_resolved_from_environment_not_assumed_app(tmp_path):
    class RootEnvironment(_Environment):
        async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
            if command == "pwd -P":
                self.commands.append((command, env))
                return ExecResult(stdout="/root\n", return_code=0)
            return await super().exec(command, cwd, env, timeout_sec, user)

    agent = MiniSweCentralAgent(logs_dir=tmp_path, model_name="test")

    resolved = await agent._resolve_cwd(RootEnvironment())

    assert resolved == "/root"
    assert agent.cwd == "/root"


@pytest.mark.asyncio
async def test_invalid_configured_cwd_falls_back_to_inherited_environment_cwd(tmp_path):
    class RootEnvironment(_Environment):
        async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
            if cwd == "/app":
                raise RuntimeError("configured cwd does not exist")
            if command == "pwd -P":
                return ExecResult(stdout="/root\n", return_code=0)
            if command.startswith("test -d "):
                return ExecResult(return_code=1)
            return await super().exec(command, cwd, env, timeout_sec, user)

    agent = MiniSweCentralAgent(logs_dir=tmp_path, model_name="test", cwd="/app")

    resolved = await agent._resolve_cwd(RootEnvironment())

    assert resolved == "/root"
    assert agent._cwd_receipt["status"] == "invalid_configured_fallback"


@pytest.mark.asyncio
async def test_oversized_changed_source_is_hydrated_and_digest_verified(tmp_path):
    payload = ("def generated():\n    return 1\n" * 12_000).encode()
    digest = hashlib.sha256(payload).hexdigest()

    class DownloadEnvironment:
        async def download_file(self, source_path, target_path):
            Path(target_path).write_bytes(payload)

    agent = MiniSweCentralAgent(logs_dir=tmp_path, model_name="test", cwd="/workspace")
    session = RepositorySession.temporary(instruction="Implement generated.py")
    transition = WorkspaceTransition(
        action_id=1,
        command="write generated.py",
        before_revision="w0",
        after_revision="w1",
        created=("generated.py",),
        after_contents={},
    )
    snapshot = WorkspaceSnapshot(
        revision="w1",
        healthy=True,
        entries={
            "generated.py": FileState("f", len(payload), "1", "1", "", digest=digest, content=None)
        },
    )

    try:
        hydrated = await agent._hydrate_graph_transition(
            DownloadEnvironment(),
            session,
            transition,
            snapshot=snapshot,
            changed_paths=("generated.py",),
            source_revision="g1",
        )
    finally:
        session.close()

    assert hydrated.after_contents["generated.py"].startswith("def generated")
    receipt = agent._repository_work_receipts[-1]
    assert receipt["kind"] == "incremental_source_transfer"
    assert receipt["status"] == "complete"
    assert receipt["digest_verified"] is True


def test_graph_transition_keeps_source_that_becomes_non_source():
    before = "#!/usr/bin/env python3\nprint('old')\n"
    after = "not source anymore\n"
    classified_after = (
        classify_change("runner", kind="f", content=after),
    )
    assert classified_after[0].graph_indexable is False
    transition = WorkspaceTransition(
        action_id=1,
        command="rewrite runner",
        before_revision="w0",
        after_revision="w1",
        modified=("runner",),
        before_contents={"runner": before},
        after_contents={"runner": after},
    )

    assert _graph_transition_paths(
        classified_after,
        transition,
        task_deliverables=(),
    ) == ("runner",)


def test_graph_transition_uses_session_mirror_when_prior_sensor_text_is_uncaptured():
    session = RepositorySession.temporary(instruction="repair runner")
    try:
        (session.root / "runner").write_text(
            "#!/usr/bin/env python3\nprint('prior large source')\n",
            encoding="utf-8",
        )
        classified_after = (
            classify_change("runner", kind="f", content="not source anymore\n"),
        )
        transition = WorkspaceTransition(
            action_id=1,
            command="rewrite runner",
            before_revision="w0",
            after_revision="w1",
            modified=("runner",),
            before_contents={},
            after_contents={"runner": "not source anymore\n"},
        )

        assert _graph_transition_paths(
            classified_after,
            transition,
            task_deliverables=(),
            repository_session=session,
        ) == ("runner",)
    finally:
        session.close()


def test_stable_provider_prefix_counts_only_exact_append_stable_messages():
    previous = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "reasoning-a"},
    ]
    appended = [*previous, {"role": "tool", "content": "result"}]
    count, chars, ratio = _stable_provider_prefix(previous, appended)

    assert count == 3
    assert chars > 0
    assert 0.0 < ratio < 1.0

    changed = [dict(previous[0]), {"role": "user", "content": "changed"}]
    assert _stable_provider_prefix(previous, changed)[0] == 1


class _ScriptedModel:
    config = type("Config", (), {"model_name": "test"})()
    tools = [
        {
            "type": "function",
            "function": {"name": "bash", "parameters": {"type": "object"}},
        }
    ]

    def __init__(self, commands):
        self.commands = iter(commands)
        self.observed: list[str] = []
        self.observed_history: list[list[str]] = []

    def format_message(self, **kwargs):
        return kwargs

    def get_template_vars(self):
        return {
            "observation_template": "{{ output.output }}",
            "format_error_template": "error",
        }

    def query(self, messages):
        self.observed = [str(item.get("content") or "") for item in messages]
        self.observed_history.append(self.observed)
        command = next(self.commands, None)
        if command is None:
            raise RuntimeError("scripted model script exhausted")
        return {
            "role": "assistant",
            "content": "act",
            "extra": {
                "actions": [{"command": command, "tool_call_id": "call-1"}],
                "response": {
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 2,
                        "total_tokens": 12,
                    }
                },
                "cost": 0.0,
            },
        }

    def format_observation_messages(self, message, outputs, template_vars=None):
        return [{"role": "tool", "content": outputs[0]["output"]}]


class _BatchModel(_ScriptedModel):
    def __init__(self, batches):
        self.batches = iter(batches)
        self.observed = []
        self.observed_history = []

    def query(self, messages):
        self.observed = [str(item.get("content") or "") for item in messages]
        self.observed_history.append(self.observed)
        commands = next(self.batches, None)
        if commands is None:
            raise RuntimeError("scripted batch model script exhausted")
        return {
            "role": "assistant",
            "content": "act",
            "extra": {
                "actions": [
                    {"command": command, "tool_call_id": f"call-{index}"}
                    for index, command in enumerate(commands, 1)
                ],
                "response": {"usage": {}},
                "cost": 0.0,
            },
        }

    def format_observation_messages(self, message, outputs, template_vars=None):
        actions = message["extra"]["actions"]
        assert len(outputs) == len(actions)
        return [
            {
                "role": "tool",
                "tool_call_id": action["tool_call_id"],
                "content": output["output"],
            }
            for action, output in zip(actions, outputs, strict=True)
        ]


@pytest.mark.asyncio
async def test_observed_fact_survives_later_empty_action_until_next_provider_request(tmp_path):
    class ObservedFactEnvironment(_Environment):
        async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
            self.commands.append((command, env))
            if command == "printf-shebang":
                return ExecResult(stdout="#!/usr/bin/env python3\n", return_code=0)
            if command == "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT":
                return ExecResult(
                    stdout="COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n", return_code=0
                )
            return ExecResult(stdout="", return_code=0)

    model = _BatchModel(
        [
            ["printf-shebang", "true"],
            ["true"],
            ["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"],
        ]
    )
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        enable_observed_facts=True,
        enable_replay_capture=True,
    )
    agent._model_factory = lambda: model
    context = AgentContext()
    await agent.run("inspect the environment and submit", ObservedFactEnvironment(), context)

    receipt = json.loads((tmp_path / "central_receipt.json").read_text(encoding="utf-8"))
    assert receipt["observed_facts"]["fact_deliveries"] == []
    assert not any(
        "Observed interpreter path: /usr/bin/env." in content
        for content in model.observed_history[1]
    )
    assert any(
        row["surface"] == "observed_execution"
        and row["disposition"] == "value_rejected"
        for call in receipt["contribution_compiler"]["calls"]
        for row in call["accounting"]
    )
    observed_rows = [
        row
        for call in receipt["contribution_compiler"]["calls"]
        for row in call["accounting"]
        if row["surface"] == "observed_execution"
    ]
    assert len(observed_rows) == 1
    assert len(receipt["observed_facts"]["fact_extractions"]) == 1
    assert receipt["observed_facts"]["max_deliveries_per_task"] == 4
    assert receipt["observed_facts"]["fact_decisions"] == [
        {
            "fact_id": receipt["observed_facts"]["fact_decisions"][0]["fact_id"],
            "kind": "shebang",
            "call": 2,
            "disposition": "value_rejected",
            "reason_codes": receipt["observed_facts"]["fact_decisions"][0][
                "reason_codes"
            ],
            "contribution_id": receipt["observed_facts"]["fact_decisions"][0][
                "contribution_id"
            ],
        }
    ]


@pytest.mark.asyncio
async def test_observed_facts_from_later_batch_actions_are_terminally_accounted(tmp_path):
    class MultiFactEnvironment(_Environment):
        async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
            self.commands.append((command, env))
            if command == "printf-shebang":
                return ExecResult(stdout="#!/usr/bin/env python3\n", return_code=0)
            if command == "python-version":
                return ExecResult(stdout="Python 3.12.0\n", return_code=0)
            if command == "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT":
                return ExecResult(
                    stdout="COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n", return_code=0
                )
            return ExecResult(stdout="", return_code=0)

    model = _BatchModel(
        [
            ["printf-shebang", "python-version"],
            ["true"],
            ["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"],
        ]
    )
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        enable_observed_facts=True,
        enable_replay_capture=True,
    )
    agent._model_factory = lambda: model

    await agent.run("inspect the environment and submit", MultiFactEnvironment(), AgentContext())

    receipt = json.loads((tmp_path / "central_receipt.json").read_text(encoding="utf-8"))
    extracted = receipt["observed_facts"]["fact_extractions"]
    decisions = receipt["observed_facts"]["fact_decisions"]
    assert {row["kind"] for row in extracted} == {"shebang", "tool_version"}
    assert {row["kind"] for row in decisions} == {"shebang", "tool_version"}
    assert next(row for row in decisions if row["kind"] == "tool_version")[
        "disposition"
    ] == "value_rejected"


@pytest.mark.asyncio
async def test_deterministic_bootstrap_mode_uses_no_provider_call(tmp_path):
    catalog = production_shaped_catalog()
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        persistent_state_selection_mode="deterministic_v1",
    )

    selection, receipt = await agent._run_persistent_state_bootstrap(
        object(),
        instruction="Select certified repository context.",
        catalog=catalog,
        timeout_sec=5,
    )

    assert selection.valid is True
    assert receipt["bootstrap_mode"] == "deterministic_selected"
    assert receipt["selection_mode"] == "deterministic_v1"
    assert receipt["selection_event_count"] == 1
    assert receipt["selection_provider_calls"] == 0
    assert receipt["provider_calls"] == 0
    assert receipt["status"] == "selected"


@pytest.mark.asyncio
async def test_persistent_state_bootstraps_once_then_runs_at_every_live_boundary(
    tmp_path, monkeypatch
):
    class BootstrapModel(_ScriptedModel):
        def __init__(self):
            super().__init__([])
            self.query_count = 0
            self.bootstrap_kwargs = None

        def query(self, messages, **kwargs):
            self.query_count += 1
            self.observed = [str(item.get("content") or "") for item in messages]
            self.observed_history.append(self.observed)
            if self.query_count == 1:
                self.bootstrap_kwargs = dict(kwargs)
                catalog_blob = (
                    self.observed[-1]
                    .split("CERTIFIED CATALOG\n", 1)[1]
                    .split("\n\nSelect only", 1)[0]
                )
                catalog = json.loads(catalog_blob)
                focus = next(item["id"] for item in catalog if item["kind"] == "focus")
                validation = next(item["id"] for item in catalog if item["kind"] == "validation")
                selection = {
                    "primary_focus_id": focus,
                    "ordered_item_ids": [focus, validation],
                    "risk_item_ids": [],
                    "validation_item_ids": [validation],
                }
                content = "bootstrap-selection"
            elif self.query_count == 2:
                command = "sed -n '1,40p' src/service.py"
                content = "inspect"
            else:
                command = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
                content = "submit"
            extra = {
                "response": {
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 2,
                        "total_tokens": 12,
                    }
                },
                "cost": 0.0,
            }
            if self.query_count == 1:
                extra["select_catalog_args"] = selection
                extra["select_catalog_raw"] = json.dumps(selection)
                extra["actions"] = []
            else:
                extra["actions"] = [
                    {"command": command, "tool_call_id": f"call-{self.query_count}"}
                ]
            return {
                "role": "assistant",
                "content": content,
                "extra": extra,
            }

    model = BootstrapModel()
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        enable_persistent_execution_state=True,
        enable_context_frontier=False,
        enable_preemptive_retrieval=True,
        enable_feature_guidance=False,
        enable_completion_controller=False,
    )
    agent._model_factory = lambda: model

    async def fake_repository_session(*args, **kwargs):
        graph_source_revision = kwargs["source_revision"]
        evidence = RepositoryEvidence(
            available=True,
            graph_revision="graph-1",
            anchors=(
                {
                    "path": "src/service.py",
                    "line": 1,
                    "symbol": "save_user",
                    "semantic_certainty": 1.0,
                    "retrieval_relevance": 1.0,
                },
            ),
            definitions=({"path": "src/service.py", "line": 1, "symbol": "save_user"},),
            project_checks=("pytest tests/test_service.py -q",),
            status="source_backed",
            source_revision=graph_source_revision,
            index_current=True,
            intelligence_valid=True,
            substrate_ready=True,
            index=IndexBuildReceipt(
                status=IndexBuildStatus.AVAILABLE,
                graph_db=str(tmp_path / "graph.db"),
                schema_valid=True,
                node_count=2,
                edge_count=0,
                source_files=1,
                indexable_files=1,
                graph_revision="graph-1",
                source_revision=graph_source_revision,
            ),
        )
        session = SimpleNamespace(
            root=tmp_path,
            indexed_source_revision=graph_source_revision,
            source_revision=graph_source_revision,
            evidence=evidence,
            refresh_log=[],
            summary=lambda: {"status": "source_backed"},
            close=lambda: None,
        )
        return evidence, session

    repository = HybridRepository(
        documents=(
            RepositoryDocument(
                path="src/service.py",
                start_line=1,
                end_line=2,
                symbol="save_user",
                text="def save_user():\n    pass",
                provenance=("graph_node",),
            ),
        ),
        structural_links=(),
        source_revision="graph-source",
        complete=True,
        reason_codes=(),
        source_file_count=1,
        document_chars=25,
    )
    query_states = []

    def build_query_repository(*args, **kwargs):
        query_states.append(kwargs["state"])
        return replace(repository, source_revision=kwargs["state"].source_revision)

    monkeypatch.setattr(
        "eval.gt_central_agent.build_query_hybrid_repository",
        build_query_repository,
    )
    agent._start_repository_session = fake_repository_session
    environment = _Environment()

    context = AgentContext()
    await agent.run(
        "Fix src/service.py and run pytest tests/test_service.py -q.",
        environment,
        context,
    )

    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    persistent = receipt["persistent_execution_state"]
    product_census = receipt["product_mechanism_census"]
    assert model.query_count == 3
    assert model.bootstrap_kwargs == {
        "temperature": 0.0,
        "max_tokens": 512,
        "tool_choice": {"type": "function", "function": {"name": "select_catalog"}},
    }
    assert receipt["bootstrap_calls"] == 1
    assert receipt["executor_calls"] == 2
    assert receipt["calls"] == 3
    assert receipt["metrics"]["api_calls"] == 3
    assert receipt["metrics"]["executor_api_calls"] == 2
    assert receipt["metrics"]["bootstrap_api_calls"] == 1
    assert receipt["metrics"]["provider_request_hash_coverage"] == 1.0
    assert receipt["metrics"]["bootstrap_provider_request_chars"] > 0
    assert context.metadata["api_calls"] == 3
    assert context.metadata["executor_api_calls"] == 2
    assert context.metadata["bootstrap_api_calls"] == 1
    assert persistent["initial_retrieval"]["calls"] == 1
    assert persistent["initial_retrieval"]["provider_calls"] == 0
    assert persistent["initial_retrieval"]["action_executions"] == 0
    assert persistent["initial_retrieval"]["ranked_files"][0]["path"] == ("src/service.py")
    assert {row["channel"] for row in persistent["initial_retrieval"]["channel_receipts"]} == {
        "exact",
        "lexical",
        "bm25",
        "dense",
        "structural",
    }
    assert receipt["metrics"]["persistent_state_initial_retrieval_calls"] == 1
    assert query_states
    assert query_states[0].task_text.startswith("Fix src/service.py")
    assert persistent["initial_retrieval"]["runtime_cache_seeded"] is True
    task_start_retrieval = receipt["preemptive_retrieval"]["decisions"][0]
    assert task_start_retrieval["opportunity_kind"] == "task_start"
    assert task_start_retrieval["cache_hit"] is False
    assert task_start_retrieval["channel_receipts"] == []
    assert persistent["bootstrap"]["action_executions"] == 0
    assert persistent["bootstrap"]["status"] == "selected"
    assert any(
        "The repository workspace for this task is /app." in content
        for content in model.observed_history[1]
    )
    assert receipt["workspace_prompt"] == {
        "contract": "resolved_workspace_v1",
        "path": "/app",
        "applied": True,
    }
    assert receipt["provider_prompt_identity"]["schema"] == (
        "gt.provider_prompt_identity.v1"
    )
    assert all(
        len(receipt["provider_prompt_identity"][key]) == 64
        for key in (
            "system_prompt_sha256",
            "task_prompt_sha256",
            "tool_schema_sha256",
        )
    )
    assert persistent["metrics"]["context_compilations"] == 2
    assert persistent["metrics"]["preflight_projections"] == 2
    assert persistent["metrics"]["postflight_commits"] == 2
    assert persistent["deliveries"] == []
    first_executor_view = "\n".join(model.observed_history[1])
    second_executor_view = "\n".join(model.observed_history[2])
    assert "Current task execution status:" not in first_executor_view
    assert "Required run_validation" not in first_executor_view
    assert "def save_user():" not in first_executor_view
    assert "Current task execution status:" not in second_executor_view
    assert "[GT repository evidence:" not in first_executor_view
    assert task_start_retrieval["status"] == "abstained"
    assert task_start_retrieval["reason_codes"] == ["persistent_bootstrap_owns_task_start"]
    assert persistent["valid"] is True
    assert product_census["legacy_feature_count"] == 17
    assert product_census["product_mechanism_count"] == 18
    assert product_census["configured_mechanism_count"] == 18
    assert product_census["persistent_execution_state"]["exercised"] is True
    assert product_census["persistent_execution_state"][
        "repeated_deterministic_use"
    ] is True
    assert product_census["persistent_execution_state"]["lifecycle_use_count"] > 1
    assert product_census["mechanism_ids"][-1] == "persistent_execution_state"
    assert not any("bootstrap-selection" in item for item in model.observed_history[1])
    executed = [command for command, _ in environment.commands]
    assert not any("primary_focus_id" in command for command in executed)
    assert executed.count("sed -n '1,40p' src/service.py") == 1
    rows, failures, totals = audit_provider_deliveries(receipt, task="persistent")
    assert failures == []
    assert "persistent_execution_state" not in totals["surfaces"]
    assert rows == []


@pytest.mark.asyncio
async def test_initial_retrieval_failure_prevents_bootstrap_provider_spend(
    tmp_path, monkeypatch
):
    model = _ScriptedModel(["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"])
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        enable_persistent_execution_state=True,
        enable_preemptive_retrieval=True,
        enable_completion_controller=False,
    )
    agent._model_factory = lambda: model

    async def fake_repository_session(*args, **kwargs):
        revision = kwargs["source_revision"]
        evidence = RepositoryEvidence(
            available=True,
            graph_revision="graph-1",
            anchors=({"path": "src/service.py", "line": 1, "symbol": "save_user"},),
            status="source_backed",
            source_revision=revision,
            index_current=True,
            intelligence_valid=True,
            substrate_ready=True,
            index=IndexBuildReceipt(
                status=IndexBuildStatus.AVAILABLE,
                graph_db=str(tmp_path / "graph.db"),
                schema_valid=True,
                node_count=1,
                source_files=1,
                indexable_files=1,
                graph_revision="graph-1",
                source_revision=revision,
            ),
        )
        return evidence, SimpleNamespace(
            root=tmp_path,
            indexed_source_revision=revision,
            source_revision=revision,
            evidence=evidence,
            refresh_log=[],
            summary=lambda: {"status": "source_backed"},
            close=lambda: None,
        )

    repository = HybridRepository(
        documents=(
            RepositoryDocument(
                path="src/service.py",
                start_line=1,
                end_line=2,
                symbol="save_user",
                text="def save_user():\n    pass",
                provenance=("graph_node",),
            ),
        ),
        structural_links=(),
        source_revision="source-1",
        complete=True,
        reason_codes=(),
        source_file_count=1,
        document_chars=25,
    )
    monkeypatch.setattr(
        "eval.gt_central_agent.build_query_hybrid_repository",
        lambda *args, **kwargs: replace(
            repository,
            source_revision=kwargs["state"].source_revision,
        ),
    )

    def fail_retrieval(*args, **kwargs):
        raise TimeoutError("five-channel retrieval timed out")

    monkeypatch.setattr("eval.gt_central_agent.HybridRetriever.retrieve", fail_retrieval)
    agent._start_repository_session = fake_repository_session

    await agent.run("Fix src/service.py.", _Environment(), AgentContext())

    receipt = json.loads((tmp_path / "central_receipt.json").read_text(encoding="utf-8"))
    persistent = receipt["persistent_execution_state"]
    assert len(model.observed_history) == 1
    assert persistent["initial_retrieval"]["status"] == "timeout"
    assert persistent["initialization"]["status"] == "initial_retrieval_unavailable"
    assert persistent["bootstrap"]["provider_calls"] == 0
    assert persistent.get("engine") is None


@pytest.mark.asyncio
async def test_bootstrap_raw_transport_marks_call_and_uses_provider_timeout_without_orphan(
    monkeypatch, tmp_path
):
    document = RepositoryDocument(
        path="src/service.py",
        start_line=1,
        end_line=2,
        symbol="save_user",
        text="def save_user():\n    pass",
    )
    evidence = RepositoryEvidence(
        available=True,
        graph_revision="graph-1",
        anchors=({"path": "src/service.py", "line": 1, "symbol": "save_user"},),
        status="source_backed",
        source_revision="source-1",
        index_current=True,
        intelligence_valid=True,
        substrate_ready=True,
    )
    catalog = build_bootstrap_catalog(
        instruction="Fix `save_user()`.",
        evidence=evidence,
        documents=(document,),
        structural_links=(),
        source_revision="source-1",
        graph_revision="graph-1",
        repository_complete=True,
    )

    class RawModel:
        config = SimpleNamespace(
            model_name="openai/deepseek-v4-flash",
            model_kwargs={"api_base": "https://api.deepseek.com"},
        )

        def __init__(self):
            self.calls = 0

        def _prepare_messages_for_api(self, messages):
            return [
                {key: value for key, value in row.items() if key != "extra"}
                for row in messages
            ]

        def _query(self, messages, **kwargs):
            self.calls += 1
            marker = json.loads(
                (tmp_path / "provider_query_started.json").read_text(encoding="utf-8")
            )
            assert marker["last_call_kind"] == "persistent_bootstrap"
            assert marker["bootstrap_calls_started"] == 1
            assert marker["executor_calls_started"] == 0
            assert kwargs["num_retries"] == 0
            assert kwargs["timeout"] == 5
            assert kwargs["tool_choice"]["function"]["name"] == "select_catalog"
            assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
            message = SimpleNamespace(
                model_dump=lambda: {"role": "assistant", "content": "plain JSON, no tool"}
            )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=message)],
                model_dump=lambda: {
                    "model": "deepseek/deepseek-v4-flash-0731",
                    "provider": "deepseek",
                    "system_fingerprint": "fp-test",
                    "usage": {"prompt_tokens": 41, "completion_tokens": 7},
                },
            )

        def _parse_actions(self, response):
            raise FormatError({"role": "user", "content": "tool required"})

        def _calculate_cost(self, response):
            return {"cost": 0.25}

    model = RawModel()
    agent = MiniSweCentralAgent(logs_dir=tmp_path, model_name="test")

    async def forbidden_wait_for(*args, **kwargs):
        raise AssertionError("bootstrap must not abandon an uncancellable provider thread")

    monkeypatch.setattr("eval.gt_central_agent.asyncio.wait_for", forbidden_wait_for)

    selection, receipt = await agent._run_persistent_state_bootstrap(
        model,
        instruction="Fix `save_user()`.",
        catalog=catalog,
        timeout_sec=5,
    )

    assert selection.valid is False
    assert selection.reason_codes == ("bootstrap_action_parse_error:bootstrap_action_count",)
    assert model.calls == 1
    assert receipt["provider_calls"] == 1
    assert receipt["response_received"] is True
    assert receipt["transport"] == "direct_single_provider_call"
    assert receipt["provider_query_marker_error"] == ""
    assert receipt["call_contract"]["forced_tool"] == "select_catalog"
    assert receipt["raw_tool_arguments_sha256"] == ""
    assert receipt["attempted_item_ids"] == []
    assert receipt["input_tokens"] == 41
    assert receipt["output_tokens"] == 7
    assert receipt["response_identity"] == {
        "model": "deepseek/deepseek-v4-flash-0731",
        "provider": "deepseek",
        "system_fingerprint": "fp-test",
    }


@pytest.mark.asyncio
async def test_executor_uses_one_direct_provider_transport_and_preserves_hidden_provider(
    tmp_path,
):
    class RawExecutorModel(_ScriptedModel):
        config = SimpleNamespace(model_name="test", model_kwargs={"num_retries": 0})

        def __init__(self):
            super().__init__([])
            self.raw_calls = 0
            self.public_calls = 0

        def query(self, messages, **kwargs):
            self.public_calls += 1
            raise AssertionError("executor must bypass Mini-SWE's retry-wrapped query")

        def _prepare_messages_for_api(self, messages):
            return [
                {key: value for key, value in row.items() if key != "extra"}
                for row in messages
            ]

        def _query(self, messages, **kwargs):
            self.raw_calls += 1
            assert kwargs["num_retries"] == 0
            message = SimpleNamespace(
                model_dump=lambda: {
                    "role": "assistant",
                    "content": "act",
                    "tool_calls": [],
                }
            )
            response = SimpleNamespace(
                choices=[SimpleNamespace(message=message)],
                _hidden_params={"custom_llm_provider": "deepseek"},
                model_dump=lambda: {
                    "model": "deepseek/deepseek-v4-flash-0731",
                    "system_fingerprint": "fp-test",
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2},
                },
            )
            return response

        def _parse_actions(self, response):
            return [
                {
                    "command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",
                    "tool_call_id": "call-1",
                }
            ]

        def _calculate_cost(self, response):
            return {"cost": 0.02}

    model = RawExecutorModel()
    agent = MiniSweCentralAgent(logs_dir=tmp_path, model_name="test")
    agent._model_factory = lambda: model

    await agent.run("Finish.", _Environment(), AgentContext())

    receipt = json.loads((tmp_path / "central_receipt.json").read_text(encoding="utf-8"))
    assert model.raw_calls == 1
    assert model.public_calls == 0
    assert receipt["provider_route"]["executor_transport"] == "direct_single_provider_call"
    assert receipt["provider_route"]["executor_retry_policy"] == "provider_once_no_retry"
    assert receipt["provider_response_identity"]["executor"]["providers"] == ["deepseek"]


@pytest.mark.asyncio
async def test_bootstrap_empty_choices_retains_received_response_accounting(tmp_path):
    document = RepositoryDocument(
        path="src/service.py",
        start_line=1,
        end_line=2,
        symbol="save_user",
        text="def save_user():\n    pass",
    )
    evidence = RepositoryEvidence(
        available=True,
        graph_revision="graph-1",
        anchors=({"path": "src/service.py", "line": 1, "symbol": "save_user"},),
        status="source_backed",
        source_revision="source-1",
        index_current=True,
        intelligence_valid=True,
        substrate_ready=True,
    )
    catalog = build_bootstrap_catalog(
        instruction="Fix `save_user()`.",
        evidence=evidence,
        documents=(document,),
        structural_links=(),
        source_revision="source-1",
        graph_revision="graph-1",
        repository_complete=True,
    )

    class EmptyChoicesModel:
        config = SimpleNamespace(model_name="test", model_kwargs={})

        def _prepare_messages_for_api(self, messages):
            return messages

        def _query(self, messages, **kwargs):
            return SimpleNamespace(
                choices=[],
                _hidden_params={"custom_llm_provider": "deepseek"},
                model_dump=lambda: {
                    "model": "deepseek/deepseek-v4-flash-0731",
                    "system_fingerprint": "fp-test",
                    "usage": {"prompt_tokens": 41, "completion_tokens": 7},
                },
            )

        def _parse_actions(self, response):
            raise AssertionError("empty choices must be typed before action parsing")

        def _calculate_cost(self, response):
            return {"cost": 0.25}

    agent = MiniSweCentralAgent(logs_dir=tmp_path, model_name="test")
    selection, receipt = await agent._run_persistent_state_bootstrap(
        EmptyChoicesModel(),
        instruction="Fix `save_user()`.",
        catalog=catalog,
        timeout_sec=5,
    )

    assert selection.valid is False
    assert selection.reason_codes == ("bootstrap_action_parse_error:EmptyChoices",)
    assert receipt["provider_calls"] == 1
    assert receipt["response_received"] is True
    assert receipt["input_tokens"] == 41
    assert receipt["output_tokens"] == 7
    assert receipt["cost"] == 0.25
    assert receipt["response_identity"]["provider"] == "deepseek"


@pytest.mark.asyncio
async def test_bootstrap_persists_raw_args_and_attempted_ids_on_invalid_json(tmp_path):
    catalog = build_bootstrap_catalog(
        instruction="Fix `save_user()`.",
        evidence=RepositoryEvidence(
            available=True,
            graph_revision="graph-1",
            anchors=({"path": "src/service.py", "line": 1, "symbol": "save_user"},),
            status="source_backed",
            source_revision="source-1",
            index_current=True,
            intelligence_valid=True,
            substrate_ready=True,
        ),
        documents=(
            RepositoryDocument(
                path="src/service.py",
                start_line=1,
                end_line=2,
                symbol="save_user",
                text="def save_user():\n    pass",
            ),
        ),
        structural_links=(),
        source_revision="source-1",
        graph_revision="graph-1",
        repository_complete=True,
    )
    raw_args = '{"primary_focus_id":"pes-0123456789abcdef0123","ordered_item_ids":['

    class InvalidJsonModel:
        config = SimpleNamespace(model_name="test", model_kwargs={})

        def _prepare_messages_for_api(self, messages):
            return messages

        def _bootstrap_query(self, messages, **kwargs):
            function = SimpleNamespace(name="select_catalog", arguments=raw_args)
            tool_call = SimpleNamespace(function=function)
            message = SimpleNamespace(
                model_dump=lambda: {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "select_catalog", "arguments": raw_args}}
                    ],
                },
                tool_calls=[tool_call],
            )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=message)],
                model_dump=lambda: {
                    "model": "test-model",
                    "provider": "test",
                    "usage": {"prompt_tokens": 3, "completion_tokens": 1},
                },
            )

        def _calculate_cost(self, response):
            return {"cost": 0.0}

    selection, receipt = await MiniSweCentralAgent(
        logs_dir=tmp_path, model_name="test"
    )._run_persistent_state_bootstrap(
        InvalidJsonModel(),
        instruction="Fix `save_user()`.",
        catalog=catalog,
        timeout_sec=5,
    )

    assert selection.valid is False
    assert "invalid_json" in selection.reason_codes[0]
    assert receipt["raw_tool_arguments_sha256"] == hashlib.sha256(
        raw_args.encode()
    ).hexdigest()
    assert receipt["attempted_item_ids"] == ["pes-0123456789abcdef0123"]
    assert "pes-0123456789abcdef0123" in receipt["raw_tool_arguments_preview"]


@pytest.mark.asyncio
async def test_bootstrap_marker_failure_prevents_provider_transport(tmp_path):
    document = RepositoryDocument(
        path="src/service.py",
        start_line=1,
        end_line=2,
        symbol="save_user",
        text="def save_user():\n    pass",
    )
    evidence = RepositoryEvidence(
        available=True,
        graph_revision="graph-1",
        anchors=({"path": "src/service.py", "line": 1, "symbol": "save_user"},),
        status="source_backed",
        source_revision="source-1",
        index_current=True,
        intelligence_valid=True,
        substrate_ready=True,
    )
    catalog = build_bootstrap_catalog(
        instruction="Fix `save_user()`.",
        evidence=evidence,
        documents=(document,),
        structural_links=(),
        source_revision="source-1",
        graph_revision="graph-1",
        repository_complete=True,
    )

    class MustNotQuery:
        config = SimpleNamespace(model_name="test", model_kwargs={})

        def _prepare_messages_for_api(self, messages):
            return messages

        def _query(self, messages, **kwargs):
            raise AssertionError("provider transport must not start without a durable marker")

    agent = MiniSweCentralAgent(logs_dir=tmp_path, model_name="test")
    agent._write_provider_query_marker = lambda **kwargs: "OSError"  # type: ignore[method-assign]

    selection, receipt = await agent._run_persistent_state_bootstrap(
        MustNotQuery(),
        instruction="Fix `save_user()`.",
        catalog=catalog,
        timeout_sec=5,
    )

    assert selection.valid is False
    assert selection.reason_codes == ("provider_query_marker_error:OSError",)
    assert receipt["provider_calls"] == 0
    assert receipt["response_received"] is False


class _ObservedMutationEnvironment(_Environment):
    def __init__(self, mutation_command: str, manifest_after: str):
        super().__init__()
        self.mutation_command = mutation_command
        self.manifest_after = manifest_after
        self.changed = False

    async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
        self.commands.append((command, env))
        if command.startswith("uname "):
            return ExecResult(stdout="Linux\t6.8\tversion\tx86_64\n", return_code=0)
        if "-printf" in command:
            return ExecResult(stdout=self.manifest_after if self.changed else "", return_code=0)
        if command.startswith("sha256sum"):
            paths = [word for word in command.split() if word.endswith(".py")]
            return ExecResult(
                stdout="".join(("a" * 64) + f"  {path}\n" for path in paths),
                return_code=0,
            )
        if command.startswith("python3 -c"):
            return ExecResult(stdout='{"app.py":"eCA9IDEK"}\n', return_code=0)
        if command == self.mutation_command:
            self.changed = True
        return ExecResult(return_code=0)


@pytest.mark.asyncio
async def test_supported_source_creation_activates_persistent_state_once(
    tmp_path, monkeypatch
):
    class TransferMutationEnvironment(_ObservedMutationEnvironment):
        async def download_dir_with_exclusions(self, *, source_dir, target_dir, exclude):
            Path(target_dir).mkdir(parents=True, exist_ok=True)

        async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
            if command.startswith("python3 -c"):
                self.commands.append((command, env))
                return ExecResult(
                    stdout='{"app.py":"ZGVmIG1haW4oKToKICAgIHJldHVybiAxCg=="}\n',
                    return_code=0,
                )
            return await super().exec(command, cwd, env, timeout_sec, user)

    class DynamicBootstrapModel(_ScriptedModel):
        def __init__(self):
            super().__init__([])
            self.query_count = 0
            self.bootstrap_calls = 0

        def query(self, messages, **kwargs):
            self.query_count += 1
            self.observed = [str(item.get("content") or "") for item in messages]
            self.observed_history.append(self.observed)
            if kwargs.get("tool_choice"):
                self.bootstrap_calls += 1
                catalog_blob = (
                    self.observed[-1]
                    .split("CERTIFIED CATALOG\n", 1)[1]
                    .split("\n\nSelect only", 1)[0]
                )
                catalog = json.loads(catalog_blob)
                focus = next(item["id"] for item in catalog if item["kind"] == "focus")
                selection = {
                    "primary_focus_id": focus,
                    "ordered_item_ids": [focus],
                    "risk_item_ids": [],
                    "validation_item_ids": [],
                }
            elif self.query_count == 1:
                command = "printf 'def main():\\n    return 1\\n' > app.py"
            else:
                command = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
            extra = {
                "response": {"usage": {"prompt_tokens": 10, "completion_tokens": 2}},
                "cost": 0.0,
            }
            if kwargs.get("tool_choice"):
                extra["select_catalog_args"] = selection
                extra["select_catalog_raw"] = json.dumps(selection)
                extra["actions"] = []
            else:
                extra["actions"] = [
                    {"command": command, "tool_call_id": f"call-{self.query_count}"}
                ]
            return {
                "role": "assistant",
                "content": "act",
                "extra": extra,
            }

    repository = HybridRepository(
        documents=(
            RepositoryDocument(
                path="app.py",
                start_line=1,
                end_line=1,
                symbol="app",
                text="def main():\n    return 1",
                provenance=("graph_node",),
            ),
        ),
        structural_links=(),
        source_revision="dynamic-source",
        complete=True,
        reason_codes=(),
        source_file_count=1,
        document_chars=5,
    )
    monkeypatch.setattr(
        "eval.gt_central_agent.build_query_hybrid_repository",
        lambda *args, **kwargs: replace(
            repository,
            source_revision=kwargs["state"].source_revision,
        ),
    )
    model = DynamicBootstrapModel()
    environment = TransferMutationEnvironment(
        "printf 'def main():\\n    return 1\\n' > app.py",
        "f\t25\t2.0\t2.0\tapp.py\t\n",
    )
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        integration_mode="active",
        enable_persistent_execution_state=True,
        enable_preemptive_retrieval=True,
        enable_context_frontier=False,
        enable_feature_guidance=False,
    )
    agent._model_factory = lambda: model

    await agent.run("Create app.py containing the implementation.", environment, AgentContext())

    receipt = json.loads((tmp_path / "central_receipt.json").read_text(encoding="utf-8"))
    persistent = receipt["persistent_execution_state"]
    activation = persistent["activation"]
    assert model.bootstrap_calls == 1
    assert activation["initial_applicability"] == "not_applicable_no_supported_source"
    assert activation["ever_applicable"] is True
    assert activation["activation_action"] == 1
    assert persistent["bootstrap"]["status"] == "selected"
    assert persistent["metrics"]["postflight_commits"] == 2
    assert persistent["state"]["files_modified"] == ["app.py"]
    assert receipt["task_semantic_substrate"]["deliveries"] == []
    assert all(
        any(
            row.get("kind") == "deliverable_state"
            and row.get("disposition") == "instruction_entailed_controller_only"
            for row in compilation.get("accounting", ())
        )
        for compilation in receipt["task_semantic_substrate"]["compilations"]
    )
    assert not any(
        meta.get("decisive")
        for row in persistent["deliveries"]
        for meta in row.get("claim_metadata", ())
    )
    assert all(
        "GroundTruth" not in text for text in model.observed_history[-1]
    )
    assert all(
        "not_applicable_no_supported_source" not in row.get("reason_codes", ())
        for row in receipt["preemptive_retrieval"]["decisions"]
        if int(row.get("call") or 0) >= int(activation["activation_call"])
    )
    assert receipt["repository_intelligence"]["denominator_excluded"] is False
    assert receipt["product_mechanism_census"]["persistent_execution_state"]["exercised"] is True
    release_checks = {
        check.name: check
        for check in audit_treatment_runtime(receipt, label="dynamic-source")
    }
    assert set(release_checks["persistent_execution_state"].failures) == {
        "dynamic-source:persistent_bootstrap_transport_not_single_call",
    }
    assert release_checks["product_mechanism_census"].passed is True


@pytest.mark.asyncio
async def test_source_backed_localization_stays_off_first_provider_call(tmp_path):
    class TransferEnvironment(_Environment):
        async def download_dir_with_exclusions(self, *, source_dir, target_dir, exclude):
            root = Path(target_dir)
            (root / "src").mkdir(parents=True)
            (root / "pyproject.toml").write_text("[project]\nname='demo'\n")
            (root / "src" / "greeter.py").write_text(
                "def greet(name: str) -> str:\n    return f'hello {name}'\n"
            )

    model = _ScriptedModel(["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"])
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        enable_task_start_advisory=True,
        enable_context_frontier=False,
        enable_replay_capture=True,
    )
    agent._model_factory = lambda: model

    await agent.run(
        "Change greet so it returns an uppercase greeting.",
        TransferEnvironment(),
        AgentContext(),
    )

    assert len(model.observed_history) == 1
    first_request = "\n".join(model.observed_history[0])
    assert "Highest-ranked source anchors:" not in first_request
    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    evidence = receipt["repository_evidence"]
    assert evidence["available"] is True
    assert not any(
        row.get("feature_id") == "GT_LOC_RESLOT"
        for row in receipt.get("guidance_deliveries") or ()
    )
    loc_receipts = [
        row
        for row in receipt["features"]["receipts"]
        if row["feature_id"] == "GT_LOC_RESLOT"
    ]
    assert loc_receipts
    assert all(row["model_visible"] is False for row in loc_receipts)
    assert receipt["metrics"]["semantic_utilization_deliveries"] == 0


@pytest.mark.asyncio
async def test_opt_in_replay_capture_is_receipted_without_changing_request(tmp_path):
    model = _ScriptedModel(["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"])
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        enable_replay_capture=True,
        enable_repository_intelligence=False,
    )
    agent._model_factory = lambda: model

    await agent.run("Complete the task.", _Environment(), AgentContext())

    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    metadata = receipt["replay_bundle"]
    assert metadata["enabled"] is True
    assert metadata["request_bodies_captured"] is True
    assert metadata["request_envelopes_captured"] is True
    assert metadata["responses_captured"] is True
    assert metadata["trajectory_replay_ready"] is True
    assert metadata["model_causal_replay_ready"] is False
    manifest = json.loads((tmp_path / "gt_replay" / "manifest.json").read_text())
    calls = (tmp_path / "gt_replay" / "calls.jsonl").read_text().splitlines()
    assert manifest["schema"] == "gt.counterfactual_replay_bundle.v3"
    assert calls


@pytest.mark.asyncio
async def test_context_frontier_advances_repository_intelligence_without_feature_advisory(
    tmp_path,
):
    class TransferEnvironment(_Environment):
        async def download_dir_with_exclusions(self, *, source_dir, target_dir, exclude):
            root = Path(target_dir)
            (root / "src").mkdir(parents=True)
            (root / "pyproject.toml").write_text("[project]\nname='demo'\n")
            (root / "src" / "greeter.py").write_text(
                "def greet(name: str) -> str:\n    return f'hello {name}'\n"
            )

    model = _ScriptedModel(["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"])
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        enable_task_start_advisory=False,
        enable_context_frontier=True,
    )
    agent._model_factory = lambda: model

    await agent.run(
        "Ensure greet returns an uppercase greeting.",
        TransferEnvironment(),
        AgentContext(),
    )

    first_request = "\n".join(model.observed_history[0])
    assert "Repository facts for the next decision:" not in first_request
    assert "def greet(name: str) -> str" not in first_request
    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    intelligence = receipt["repository_intelligence"]
    assert intelligence["status"] == "passed"
    assert intelligence["frontier_deliveries"] == []
    assert receipt["metrics"]["context_frontier_chars_added"] == 0
    assert receipt["metrics"]["semantic_utilization_deliveries"] == 0
    assert receipt["metrics"]["context_frontier_zero_tasks"] == 1
    assert any(
        row["surface"] == "graph_frontier" and row["disposition"] == "value_rejected"
        for row in receipt["contribution_compiler"]["calls"][0]["accounting"]
    )
    assert receipt["metrics"]["repository_mirror_files"] == 2
    assert receipt["metrics"]["repository_mirror_bytes"] > 0
    assert receipt["metrics"]["repository_mirror_transfer_ms"] >= 0
    assert receipt["metrics"]["repository_index_refresh_ms"] > 0
    assert receipt["metrics"]["repository_full_refreshes"] == 1
    assert [row["kind"] for row in receipt["repository_work_receipts"]] == [
        "mirror_transfer",
        "initial_index",
    ]


@pytest.mark.asyncio
async def test_preemptive_hybrid_retrieval_reaches_exact_first_provider_request(
    tmp_path,
):
    class TransferEnvironment(_Environment):
        async def download_dir_with_exclusions(self, *, source_dir, target_dir, exclude):
            root = Path(target_dir)
            (root / "src").mkdir(parents=True)
            (root / "tests").mkdir(parents=True)
            (root / "pyproject.toml").write_text("[project]\nname='demo'\n")
            (root / "src" / "greeter.py").write_text(
                "def greet(name: str) -> str:\n    return f'hello {name}'\n"
            )
            (root / "tests" / "test_greeter.py").write_text(
                "from src.greeter import greet\n\ndef test_greet():\n    assert greet('Ada')\n"
            )

    model = _ScriptedModel(["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"])
    model_dir = os.environ.get("GT_TEST_SNOWFLAKE_MODEL_DIR") or None
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        integration_mode="active",
        enable_preemptive_retrieval=True,
        enable_task_start_advisory=False,
        enable_context_frontier=False,
        enable_feature_guidance=False,
        enable_replay_capture=True,
        preemptive_retrieval_model_dir=model_dir,
    )
    agent._model_factory = lambda: model

    await agent.run(
        "Change the greet implementation in src/greeter.py and preserve its test contract.",
        TransferEnvironment(),
        AgentContext(),
    )

    assert len(model.observed_history) == 1
    first_request = "\n".join(model.observed_history[0])
    assert "Repository facts for the next decision:" not in first_request
    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    retrieval = receipt["preemptive_retrieval"]
    assert retrieval["deliveries"] == []
    call = receipt["model_call_contexts"][0]
    assert call["control_provider_messages_sha256"]
    assert call["control_request_payload_sha256"]
    assert call["control_provider_messages_sha256"] != call["provider_messages_sha256"]
    assert call["task_semantic_substrate_delivered"] is True
    assert "Required validation command: pytest -q" in first_request
    _delivery_rows, delivery_failures, delivery_totals = audit_provider_deliveries(
        receipt, task="preemptive-integration"
    )
    assert delivery_failures == []
    assert delivery_totals["delivery_count"] == 1
    assert delivery_totals["surfaces"]["task_semantic_substrate"]["delivery_count"] == 1
    assert receipt["metrics"]["preemptive_retrieval_deliveries"] == 0
    assert receipt["metrics"]["preemptive_retrieval_claims_delivered"] == 0
    assert receipt["metrics"]["preemptive_retrieval_duplicate_claims"] == 0
    compiler = receipt["contribution_compiler"]
    assert compiler["candidate_count"] == compiler["accounted_count"]
    assert compiler["calls"][0]["selected_surfaces"] == ["task_semantic_substrate"]
    replay = load_replay_bundle(tmp_path / "gt_replay")
    pair_validation = validate_decision_point_row(
        replay["calls"][0], task_id="preemptive-retrieval"
    )
    assert pair_validation.validity is DecisionPointValidity.VALID
    assert pair_validation.case is not None
    assert "Required validation command: pytest -q" in pair_validation.case.payload
    if model_dir:
        dense = next(
            row
            for row in receipt["preemptive_retrieval"]["decisions"][0]["channel_receipts"]
            if row["channel"] == "dense"
        )
        assert dense["available"] is True
        assert dense["failed"] is False
        assert dense["backend_identity"].startswith(
            "snowflake_onnx:Snowflake/snowflake-arctic-embed-m@sha256:"
        )


@pytest.mark.asyncio
async def test_relational_v2_delivers_certified_process_after_existing_read_action(tmp_path):
    from scripts.render_treatment_agent_args import build_runtime_arguments

    class TransferEnvironment(_Environment):
        async def download_dir_with_exclusions(self, *, source_dir, target_dir, exclude):
            root = Path(target_dir)
            (root / "src").mkdir(parents=True)
            (root / "tests").mkdir(parents=True)
            (root / "src" / "entry.py").write_text(
                "from src.core import work\n\ndef run():\n    return work()\n"
            )
            (root / "src" / "core.py").write_text("def work():\n    return 1\n")
            (root / "tests" / "test_core.py").write_text(
                "from src.core import work\n\ndef test_work():\n    assert work() == 1\n"
            )

    model = _ScriptedModel(
        [
            "cat src/core.py",
            "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",
        ]
    )
    descriptor = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "eval/treatments/tb2_central_relational_v2.json"
        ).read_text(encoding="utf-8")
    )
    runtime_contract = build_runtime_arguments(
        descriptor,
        source_sha="a" * 40,
        max_steps=100,
    )
    runtime_contract_path = tmp_path / "treatment-runtime.json"
    runtime_contract_path.write_text(json.dumps(runtime_contract), encoding="utf-8")
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        benchmark_identity={"benchmark_id": "fixture-benchmark"},
        treatment_runtime_contract_path=str(runtime_contract_path),
        **runtime_contract["agent_kwargs"],
    )

    class FakeDenseBackend:
        identity = "fake-dense-v1"

        def embed_query(self, text):
            del text
            return (1.0, 0.0)

        def embed_documents(self, texts):
            return tuple((1.0, 0.0) for _ in texts)

        def receipt(self):
                return {
                    "backend": "snowflake_onnx",
                    "model_name": SNOWFLAKE_MODEL_NAME,
                    "model_revision": SNOWFLAKE_MODEL_REVISION,
                    "model_sha256": SNOWFLAKE_MODEL_SHA256,
                    "tokenizer_sha256": SNOWFLAKE_TOKENIZER_SHA256,
                    "pooling": "cls",
                    "normalization": "l2",
                    "max_length": SNOWFLAKE_MAX_LENGTH,
                    "available": True,
                "failed": False,
                "network_calls": 0,
                "provider_calls": 0,
            }

    agent._preemptive_dense_backend = FakeDenseBackend()
    agent._model_factory = lambda: model

    await agent.run(
        "Inspect src/core.py and preserve callers and tests.",
        TransferEnvironment(),
        AgentContext(),
    )

    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    assert receipt["benchmark_identity"] == {"benchmark_id": "fixture-benchmark"}
    assert receipt["treatment_profile"] == "central_relational_v2"
    assert receipt["bootstrap_calls"] == 0
    assert receipt["persistent_state_bootstrap"]["selection_mode"] == "deterministic_v1"
    census = receipt["product_mechanism_census"]
    assert census["schema"] == "gt.product_mechanism_census.v1"
    assert census["profile_id"] == "central_relational_v2"
    assert census["accounting_contract"] == "17_legacy_features_plus_1_persistent_state"
    assert "persistent_execution_state" in census["mechanism_ids"]
    assert "relational_context_state" not in census["mechanism_ids"]
    assert census["persistent_execution_state"]["configured"] is True
    assert census["persistent_execution_state"]["exercised"] is True
    assert census["persistent_execution_state"]["bootstrap_calls"] == 0
    assert census["persistent_execution_state"]["selection_mode"] == "deterministic_v1"
    assert census["persistent_execution_state"]["selection_event_count"] == 1
    assert census["persistent_execution_state"]["selection_provider_calls"] == 0
    assert census["persistent_execution_state"]["bootstrap_provider_calls"] == 0
    assert receipt["relational_context"]["enabled"] is True
    assert receipt["component_configuration"]["relational_context_profile"] == {
        "profile_id": "relational-context-v1",
        "max_depth": 6,
        "max_branching": 3,
        "max_processes": 3,
        "max_tokens": 256,
    }
    assert receipt["component_configuration"]["repository_context_profile"] == {
        "profile_id": "gt.action_local_repository_context.v1",
        "max_execution_views": 3,
        "max_relation_facts": 3,
        "max_semantic_items": 3,
        "max_edge_expansions": 256,
        "delivery_mode": "integrated_same_observation",
    }
    assert receipt["relational_context"]["deliveries"] == []
    assert receipt["metrics"]["relational_context_deliveries"] == 0
    assert receipt["semantic_evidence"]["enabled"] is True
    assert receipt["semantic_evidence"]["deliveries"] == []
    assert receipt["metrics"]["semantic_evidence_deliveries"] == 0
    assert receipt["repository_context"]["enabled"] is True
    assert receipt["repository_context"]["deliveries"]
    mechanical = receipt["mechanical_completeness"]
    assert mechanical["required"] is True
    assert len(mechanical["provider_barriers"]) == receipt["executor_calls"]
    assert all(row["status"] == "PASS" for row in mechanical["provider_barriers"])
    certificate = receipt["task_execution_certificate"]
    assert certificate["schema"] == "gt.task_execution_certificate.v1"
    # The scripted fixture has no real provider route/transport.  The
    # repository/process mechanics pass, while the full task certificate must
    # honestly remain blocked on that missing production-only identity.
    assert certificate["status"] == "BLOCKED"
    failed_requirements = {
        row["requirement_id"]: row
        for row in certificate["requirements"]
        if row["status"] == "FAILED"
    }
    assert set(failed_requirements) == {"provider_route_integrity"}
    assert certificate["pending_requirement_count"] == 0
    assert certificate["failed_requirement_count"] == 1
    assert set(certificate["failures"]) == {
        "runtime-task:provider_route_id_invalid",
        "runtime-task:provider_api_base_invalid",
        "runtime-task:provider_retry_policy_unverified",
    }
    assert receipt["task_artifact_integrity"]["status"] == "PASS"
    assert receipt["metrics"]["repository_context_deliveries"] >= 1
    assert receipt["preemptive_retrieval"]["deliveries"] == []
    assert receipt["component_configuration"]["retrieval_delivery_mode"] == (
        "integrated_same_observation"
    )
    assert receipt["component_configuration"]["replay_capture"] is True
    assert receipt["intervention_chain"]["schema"] == "gt.intervention_chain.v2"
    assert receipt["intervention_chain"]["hidden_reasoning_inferred"] is False
    assert (tmp_path / "intervention_chain.json").exists()
    assert all(
        decision["delivery_mode"] == "integrated_same_observation"
        for decision in receipt["preemptive_retrieval"]["decisions"]
    )
    assert receipt["metrics"]["preemptive_retrieval_shared_computations"] >= 1
    assert receipt["metrics"]["preemptive_retrieval_rank_consumptions"] >= 1
    assert any(
        decision["retrieval_rank_hint_count"] >= 1
        for decision in receipt["repository_context"]["decisions"]
    )
    second_request = "\n".join(model.observed_history[1])
    assert "Current certified repository context" in second_request
    assert "src/core.py" in second_request
    assert "Definition src/core.py" not in second_request
    assert "src/entry.py" in second_request
    assert "tests/test_core.py" in second_request
    assert "repository_context" in receipt["model_call_contexts"][1][
        "selected_surfaces"
    ]
    _rows, failures, totals = audit_provider_deliveries(
        receipt, task="relational-v2-integration"
    )
    assert failures == []
    assert totals["surfaces"]["repository_context"]["delivery_count"] == 1


@pytest.mark.asyncio
async def test_final_profile_invalidates_treatment_but_dispatches_baseline_solver(tmp_path):
    model = _ScriptedModel(["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"])
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        integration_mode="active",
        treatment_profile="central_relational_v2",
        enable_replay_capture=True,
    )
    agent._model_factory = lambda: model

    await agent.run("Create the requested output.", _Environment(), AgentContext())

    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    assert len(model.observed_history) == 1
    assert receipt["metrics"]["solver_exhausted_reason"] != (
        "mechanical_completeness_barrier"
    )
    barrier = receipt["mechanical_completeness"]["provider_barriers"][0]
    assert barrier["status"] == "BLOCKED"
    assert "runtime_contract_missing" in barrier["failures"]
    assert receipt["treatment_validity"]["state"] == "INVALID"
    assert "runtime_contract_missing" in receipt["treatment_validity"]["reason_codes"]
    assert receipt["model_call_contexts"][0]["provider_dispatch_assessment"] == {
        "schema": "gt.provider_dispatch_assessment.v1",
        "dispatch_allowed": True,
        "treatment_validity": "INVALID",
        "reason_codes": barrier["failures"],
    }
    assert receipt["task_execution_certificate"]["status"] == "BLOCKED"


@pytest.mark.asyncio
async def test_closed_preemptive_task_budget_skips_retrieval_before_channels(tmp_path):
    class TransferEnvironment(_Environment):
        async def download_dir_with_exclusions(self, *, source_dir, target_dir, exclude):
            root = Path(target_dir)
            (root / "src").mkdir(parents=True)
            (root / "src" / "greeter.py").write_text("def greet(): return 'hello'\n")

    model = _ScriptedModel(["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"])
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        integration_mode="active",
        enable_preemptive_retrieval=True,
        enable_task_start_advisory=False,
        enable_context_frontier=False,
        enable_feature_guidance=False,
        preemptive_retrieval_task_budget_chars=0,
    )
    agent._model_factory = lambda: model

    await agent.run("Change src/greeter.py.", TransferEnvironment(), AgentContext())

    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    decision = receipt["preemptive_retrieval"]["decisions"][0]
    assert decision["status"] == "abstained"
    assert decision["reason_codes"] == ["task_character_budget_closed_precheck"]
    assert decision["channel_receipts"] == []
    assert receipt["metrics"]["preemptive_retrieval_budget_closed_calls"] == 1


@pytest.mark.asyncio
async def test_partial_character_budget_never_selects_then_discards_a_frame(tmp_path):
    class TransferEnvironment(_Environment):
        async def download_dir_with_exclusions(self, *, source_dir, target_dir, exclude):
            root = Path(target_dir)
            (root / "src").mkdir(parents=True)
            (root / "src" / "greeter.py").write_text(
                "def greet(name: str) -> str:\n    return f'hello {name}'\n"
            )

    model = _ScriptedModel(["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"])
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        integration_mode="active",
        enable_preemptive_retrieval=True,
        enable_task_start_advisory=False,
        enable_context_frontier=False,
        enable_feature_guidance=False,
        preemptive_retrieval_task_budget_chars=16,
        preemptive_retrieval_priority_reserve_chars=0,
    )
    agent._model_factory = lambda: model

    await agent.run("Change src/greeter.py greet.", TransferEnvironment(), AgentContext())

    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    decision = receipt["preemptive_retrieval"]["decisions"][0]
    assert decision["channel_receipts"]
    assert decision["selected_evidence"] == []
    assert decision["selected_character_count"] == 0
    assert decision["remaining_budget_chars"] == 16
    assert "no_decision_relevant_evidence" in decision["reason_codes"]
    assert "task_character_budget" not in decision["reason_codes"]
    assert receipt["preemptive_retrieval"]["deliveries"] == []


@pytest.mark.asyncio
async def test_repeated_unchanged_retrieval_state_reuses_bounded_cache(tmp_path):
    class TransferEnvironment(_Environment):
        async def download_dir_with_exclusions(self, *, source_dir, target_dir, exclude):
            root = Path(target_dir)
            (root / "src").mkdir(parents=True)
            (root / "src" / "greeter.py").write_text("def greet(): return 'hello'\n")

    model = _ScriptedModel(
        [
            "cat src/greeter.py",
            "cat src/greeter.py",
            "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",
        ]
    )
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        integration_mode="active",
        enable_preemptive_retrieval=True,
        enable_task_start_advisory=False,
        enable_context_frontier=False,
        enable_feature_guidance=False,
    )
    agent._model_factory = lambda: model

    await agent.run("Inspect src/greeter.py.", TransferEnvironment(), AgentContext())

    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    decisions = receipt["preemptive_retrieval"]["decisions"]
    assert len(decisions) == 3
    assert decisions[1]["opportunity_kind"] == "post_read_search"
    assert decisions[2]["opportunity_kind"] == "post_read_search"
    assert decisions[1]["cache_hit"] is False
    assert decisions[2]["cache_hit"] is True
    assert receipt["metrics"]["preemptive_retrieval_cache_hits"] == 1
    accounting = receipt["preemptive_retrieval"]["opportunity_accounting"]
    assert accounting["opportunities"] == 3
    assert accounting["by_kind"]["post_read_search"]["cache_hits"] == 1


@pytest.mark.asyncio
async def test_priority_reserve_skips_low_value_work_but_preserves_later_diagnostic(tmp_path):
    class TransferEnvironment(_Environment):
        async def download_dir_with_exclusions(self, *, source_dir, target_dir, exclude):
            root = Path(target_dir)
            (root / "src").mkdir(parents=True)
            (root / "src" / "greeter.py").write_text("def greet(): return 'hello'\n")

        async def exec(self, command, env=None, **kwargs):
            self.commands.append((command, env))
            if command == "false":
                return ExecResult(return_code=1, stdout="", stderr="failure in greet")
            return ExecResult(return_code=0, stdout="ok", stderr="")

    model = _ScriptedModel(["false", "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"])
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        integration_mode="active",
        enable_preemptive_retrieval=True,
        enable_task_start_advisory=False,
        enable_context_frontier=False,
        enable_feature_guidance=False,
        preemptive_retrieval_task_budget_chars=500,
        preemptive_retrieval_priority_reserve_chars=500,
    )
    agent._model_factory = lambda: model

    await agent.run("Repair greet in src/greeter.py.", TransferEnvironment(), AgentContext())

    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    decisions = receipt["preemptive_retrieval"]["decisions"]
    assert decisions[0]["reason_codes"] == ["opportunity_budget_reserved_precheck"]
    assert decisions[0]["channel_receipts"] == []
    assert decisions[1]["opportunity_kind"] == "post_diagnostic"
    assert decisions[1]["channel_receipts"]


@pytest.mark.asyncio
async def test_action_conditioned_missing_evidence_returns_before_mutation_once(tmp_path):
    class TransferEnvironment(_Environment):
        async def download_dir_with_exclusions(self, *, source_dir, target_dir, exclude):
            root = Path(target_dir)
            (root / "src").mkdir(parents=True)
            (root / "src" / "greeter.py").write_text(
                "def greet(name: str) -> str:\n    return f'hello {name}'\n"
            )

    original = "rm src/greeter.py"
    revised = "rm -f src/greeter.py"
    submit = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
    model = _ScriptedModel([original, revised, submit])
    environment = TransferEnvironment()
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        integration_mode="active",
        preflight_mode="assistive_safe",
        enable_preemptive_retrieval=True,
        enable_decision_sufficiency=True,
        enable_task_start_advisory=False,
        enable_context_frontier=False,
        enable_feature_guidance=False,
    )
    agent._model_factory = lambda: model

    await agent.run("Make the requested behavior change.", environment, AgentContext())

    executed = [command for command, _env in environment.commands]
    assert original in executed
    assert revised in executed
    assert len(model.observed_history) == 3
    assert executed.count(submit) == 1
    assert "[GT certified evidence: src/greeter.py:" not in "\n".join(model.observed_history[1])
    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    assert receipt["metrics"]["submit_holds"] == 0
    decisions = receipt["decision_sufficiency"]["decisions"]
    assert decisions[0]["disposition"] == "pass"
    assert decisions[0]["applied_disposition"] == "pass"
    assert decisions[1]["disposition"] == "pass"
    assert decisions[1]["disposition"] == "pass"


@pytest.mark.asyncio
async def test_action_conditioned_decision_is_observation_only_in_shadow(tmp_path):
    class TransferEnvironment(_Environment):
        async def download_dir_with_exclusions(self, *, source_dir, target_dir, exclude):
            root = Path(target_dir)
            (root / "src").mkdir(parents=True)
            (root / "src" / "greeter.py").write_text("def greet():\n    return 'hello'\n")

    mutation = "rm src/greeter.py"
    model = _ScriptedModel([mutation, "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"])
    environment = TransferEnvironment()
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        integration_mode="active",
        preflight_mode="shadow",
        enable_preemptive_retrieval=True,
        enable_decision_sufficiency=True,
        enable_context_frontier=False,
        enable_feature_guidance=False,
    )
    agent._model_factory = lambda: model

    await agent.run("Make the requested behavior change.", environment, AgentContext())

    assert mutation in [command for command, _env in environment.commands]
    assert len(model.observed_history) == 2
    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    decision = receipt["decision_sufficiency"]["decisions"][0]
    assert decision["disposition"] == "pass"
    assert decision["applied_disposition"] == "pass"


@pytest.mark.skipif(
    not os.environ.get("GT_TEST_SNOWFLAKE_MODEL_DIR"),
    reason="real pinned Snowflake ONNX asset is not provisioned",
)
@pytest.mark.asyncio
async def test_live_snowflake_retrieval_is_cold_once_then_steady_state(tmp_path):
    class TransferEnvironment(_Environment):
        async def download_dir_with_exclusions(self, *, source_dir, target_dir, exclude):
            root = Path(target_dir)
            (root / "src").mkdir(parents=True)
            (root / "tests").mkdir(parents=True)
            (root / "src" / "greeter.py").write_text(
                "def greet(name: str) -> str:\n    return f'hello {name}'\n"
            )
            (root / "tests" / "test_greeter.py").write_text(
                "from src.greeter import greet\n\ndef test_greet():\n    assert greet('Ada')\n"
            )

    model = _ScriptedModel(
        [
            "sed -n '1,80p' src/greeter.py",
            "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",
        ]
    )
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        integration_mode="active",
        enable_preemptive_retrieval=True,
        enable_task_start_advisory=False,
        enable_context_frontier=False,
        enable_feature_guidance=False,
        preemptive_retrieval_model_dir=os.environ["GT_TEST_SNOWFLAKE_MODEL_DIR"],
    )
    agent._model_factory = lambda: model

    await agent.run(
        "Change greet in src/greeter.py and preserve the regression test.",
        TransferEnvironment(),
        AgentContext(),
    )

    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    decisions = receipt["preemptive_retrieval"]["decisions"]
    assert len(decisions) == 2
    assert decisions[0]["cold_start"] is True
    assert decisions[0]["timeout_sec"] == 30.0
    assert decisions[1]["cold_start"] is False
    assert decisions[1]["timeout_sec"] == 2.0
    assert "preemptive_retrieval_timeout" not in decisions[1]["reason_codes"]
    dense = next(row for row in decisions[1]["channel_receipts"] if row["channel"] == "dense")
    assert dense["available"] is True
    assert dense["failed"] is False


@pytest.mark.asyncio
async def test_context_frontier_exposes_path_only_evidence_without_symbol_leak(tmp_path):
    """A path need receives a location without an unrequested ranked symbol."""

    model = _ScriptedModel(["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"])
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        enable_task_start_advisory=False,
        enable_context_frontier=True,
    )
    agent._model_factory = lambda: model

    async def fake_repository_session(*args, **kwargs):
        return (
            RepositoryEvidence(
                available=True,
                graph_revision="g1",
                anchors=(
                    {
                        "path": "legacy.cob",
                        "line": 42,
                        "symbol": "WRITE-RECORD",
                        "semantic_certainty": 1.0,
                        "retrieval_relevance": 1.0,
                    },
                ),
                status="source_backed",
                # The fake deliberately leaves the revision unbound; the
                # compiler accepts it and binds the fact to the agent's
                # current source revision for this boundary test.
                source_revision="",
                index_current=True,
                intelligence_valid=True,
                substrate_ready=True,
            ),
            None,
        )

    agent._start_repository_session = fake_repository_session
    await agent.run("Update the record writer in legacy.cob.", _Environment(), AgentContext())

    assert not any("legacy.cob:42" in item for item in model.observed_history[0])
    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    deliveries = receipt["repository_intelligence"]["frontier_deliveries"]
    assert deliveries == []
    call = receipt["model_call_contexts"][0]
    assert call["stock_provider_messages_sha256"] == call["provider_messages_sha256"]
    assert call["provider_changed_message_indices"] == []
    assert call["certified_graph_chars"] == 0
    assert any(
        row["surface"] == "graph_frontier" and row["disposition"] == "value_rejected"
        for row in receipt["contribution_compiler"]["calls"][0]["accounting"]
    )


@pytest.mark.asyncio
async def test_source_less_task_is_denominator_excluded_not_graph_invalid(
    tmp_path,
):
    class TransferEnvironment(_Environment):
        async def download_dir_with_exclusions(self, *, source_dir, target_dir, exclude):
            Path(target_dir, "README.md").write_text("no structurally supported source\n")

    model = _ScriptedModel(["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"])
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        integration_mode="active",
        enable_preemptive_retrieval=True,
        enable_context_frontier=True,
    )
    agent._model_factory = lambda: model

    await agent.run("Create /app/out.json.", TransferEnvironment(), AgentContext())

    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    intelligence = receipt["repository_intelligence"]
    assert intelligence["status"] == "not_applicable"
    assert intelligence["applicability"] == "not_applicable_no_supported_source"
    assert intelligence["denominator_excluded"] is True
    assert intelligence["failures"] == []
    assert intelligence["graph_gate"]["failures"] == []
    assert intelligence["frontier_deliveries"] == []
    preemptive = receipt["preemptive_retrieval"]
    assert preemptive["deliveries"] == []
    assert preemptive["decisions"][0]["status"] == "abstained"
    assert preemptive["decisions"][0]["reason_codes"] == ["not_applicable_no_supported_source"]
    call = receipt["model_call_contexts"][0]
    assert call["control_provider_messages_sha256"] == call["provider_messages_sha256"]
    assert call["control_request_payload_sha256"] == call["request_payload_sha256"]
    assert call["task_semantic_substrate_delivered"] is False
    assert not any("Current task evidence:" in item for item in model.observed_history[0])
    semantic = receipt["task_semantic_substrate"]
    assert semantic["deliveries"] == []
    assert any(
        row["kind"] == "deliverable_state"
        and row["disposition"] == "instruction_entailed_controller_only"
        for row in semantic["compilations"][0]["accounting"]
    )
    assert receipt["metrics"]["repository_intelligence_valid"] == 0
    assert receipt["metrics"]["repository_graph_schema_valid"] == 0
    assert receipt["metrics"]["context_frontier_zero_tasks"] == 0
    persistent_census = receipt["product_mechanism_census"]["persistent_execution_state"]
    assert persistent_census["applicable"] is False
    assert persistent_census["correctly_abstained"] is True
    assert persistent_census["exercised"] is False


@pytest.mark.asyncio
async def test_assistive_convergence_returns_forbidden_artifact_read_before_execution(
    tmp_path,
):
    model = _ScriptedModel(
        [
            "cat /logs/verifier/output.txt",
            "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",
        ]
    )
    environment = _Environment()
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        integration_mode="active",
        preflight_mode="assistive_safe",
        enable_submit_readiness=False,
        enable_completion_controller=False,
    )
    agent._model_factory = lambda: model

    await agent.run("Inspect the workspace and report.", environment, AgentContext())

    executed = [command for command, _ in environment.commands]
    assert "cat /logs/verifier/output.txt" not in executed
    returned_view = "\n".join(model.observed_history[1]).lower()
    assert "current task evidence:" in returned_view
    assert "task evidence boundary" in returned_view
    assert "benchmark" not in returned_view
    assert "harness" not in returned_view
    assert "grader" not in returned_view
    assert "pre-execution check" not in returned_view
    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    convergence = receipt["convergence_controller"]
    assert convergence["return_candidates"] == 1
    assert convergence["applied_returns"] == 1
    assert any(
        "forbidden_benchmark_artifact_path" in row["reason_codes"]
        for row in convergence["preflights"]
    )


def test_tb2_workflow_gates_matrix_with_exact_runtime_bootstrap_canary():
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "tb2_miniswe_central.yml"
    ).read_text(encoding="utf-8")

    assert "python -m scripts.central_bootstrap_canary" in workflow
    assert "Reply with the single word: ok" not in workflow
    assert "--output bootstrap-canary.json" in workflow
    assert "uses: ./.github/workflows/central_provider_free.yml" in workflow
    assert "needs: [resolve, provider_free]" in workflow
    assert workflow.count("ref: ${{ needs.resolve.outputs.sha }}") == 5
    assert "name: bootstrap-canary-${{ github.run_id }}" in workflow
    provider_free = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "central_provider_free.yml"
    ).read_text(encoding="utf-8")
    assert "scripts/central_bootstrap_canary.py" in provider_free


def test_exact_bootstrap_canary_fails_closed_on_missing_provider_identity():
    result = {
        "model_effective": "openai/deepseek-v4-flash",
        "selection_valid": True,
        "receipt": {
            "status": "selected",
            "logical_calls": 1,
            "provider_calls": 1,
            "action_executions": 0,
            "response_received": True,
            "transport": "direct_single_provider_call",
            "provider_query_marker_error": "",
            "request_payload_sha256": "a" * 64,
            "provider_messages_sha256": "b" * 64,
            "visible_catalog_count": 8,
            "visible_catalog_ids_sha256": "c" * 64,
            "catalog_count": 32,
            "raw_tool_arguments_sha256": "d" * 64,
            "call_contract": {
                "thinking_mode": "disabled",
                "forced_tool": "select_catalog",
                "tool_choice": "named_function",
                "num_retries": 0,
            },
            "response_identity": {"model": "deepseek-v4-flash", "provider": ""},
        },
    }

    assert validate_canary(result) == ("provider_identity_missing",)


def test_production_shaped_canary_catalog_truncates_visible_ids():
    catalog = production_shaped_catalog()
    messages = build_bootstrap_messages(
        task="Select the certified implementation focus and related files.",
        catalog=catalog,
        max_input_tokens=2_000,
    )
    visible = bootstrap_visible_item_ids(messages)

    assert len(catalog.items) >= 16
    assert 0 < len(visible) < len(catalog.items)
    assert "select_catalog" in json.dumps(messages)


def test_native_deepseek_bootstrap_thinking_adapter_is_call_only(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("GT_LITELLM_MODEL", "openai/deepseek-v4-flash")
    monkeypatch.delenv("GT_OPENROUTER_PROVIDER_ONLY", raising=False)

    agent = MiniSweCentralAgent(logs_dir=tmp_path, model_name="deepseek-v4-flash")
    model = agent._build_model()
    kwargs = _bootstrap_provider_call_kwargs(model, max_tokens=512, timeout_sec=5)

    assert "extra_body" not in model.config.model_kwargs
    assert kwargs["tool_choice"]["function"]["name"] == "select_catalog"
    assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
    assert _provider_route_configuration(model)["thinking_mode"] == ""


@pytest.mark.asyncio
async def test_progress_delivery_uses_authoritative_provider_receipt_schema(tmp_path):
    submit = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
    model = _ScriptedModel([*("true" for _ in range(12)), submit])
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        integration_mode="active",
        enable_progress_control=True,
    )
    agent._model_factory = lambda: model

    await agent.run("Inspect the environment and finish.", _Environment(), AgentContext())

    receipt = json.loads((tmp_path / "central_receipt.json").read_text(encoding="utf-8"))
    progress = receipt["progress"]["fact_deliveries"]
    assert progress == []
    rows, failures, _ = audit_provider_deliveries(receipt, task="progress")
    assert rows == []
    assert failures == []
    assert any(
        row["surface"] == "progress_frame" and row["disposition"] == "value_rejected"
        for call in receipt["contribution_compiler"]["calls"]
        for row in call["accounting"]
    )


@pytest.mark.asyncio
async def test_task_graph_failure_degrades_but_preserves_provider_loop(tmp_path):
    class TransferEnvironment(_Environment):
        async def download_dir_with_exclusions(self, *, source_dir, target_dir, exclude):
            Path(target_dir, "README.md").write_text(
                "the task has no structurally indexable source\n"
            )

    model = _ScriptedModel(["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"])
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        enable_context_frontier=True,
        require_graph_ready=True,
    )
    agent._model_factory = lambda: model

    await agent.run("Fix the repository implementation.", TransferEnvironment(), AgentContext())

    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    assert len(model.observed_history) == 1
    assert receipt["calls"] == 1
    assert receipt["metrics"]["repository_graph_gate_enabled"] == 1
    assert receipt["metrics"]["repository_graph_gate_blocked"] == 0
    assert receipt["metrics"]["repository_graph_degraded_fallback"] == 0
    assert receipt["metrics"]["repository_graph_gate_failures"] == []
    assert receipt["metrics"]["api_calls"] == 1


@pytest.mark.asyncio
async def test_paid_environment_path_transfers_only_selected_source_files(tmp_path):
    class SourceArchiveEnvironment(_Environment):
        async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
            self.commands.append((command, env))
            if command.startswith("uname "):
                return ExecResult(stdout="Linux\t6.8\tversion\tx86_64\n", return_code=0)
            if "-printf" in command:
                return ExecResult(
                    stdout=(
                        "f\t40\t1.0\t1.0\tapp.py\t\n"
                        "f\t498000000\t1.0\t1.0\tgpt2-124M.ckpt\t\n"
                        "f\t1000000\t1.0\t1.0\tvocab.bpe\t\n"
                    ),
                    return_code=0,
                )
            if command.startswith("sha256sum"):
                return ExecResult(stdout=("a" * 64) + "  app.py\n", return_code=0)
            return ExecResult(stdout="", return_code=0)

        async def download_file(self, source_path, target_path):
            payload = b"def solve():\n    return 1\n"
            with tarfile.open(target_path, "w:gz") as archive:
                member = tarfile.TarInfo("app.py")
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))

    model = _ScriptedModel(["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"])
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        enable_context_frontier=False,
        require_graph_ready=False,
    )
    agent._model_factory = lambda: model

    environment = SourceArchiveEnvironment()
    await agent.run("Fix solve in app.py.", environment, AgentContext())

    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    plan = next(
        row for row in receipt["repository_work_receipts"] if row["kind"] == "source_mirror_plan"
    )
    transfer = next(
        row for row in receipt["repository_work_receipts"] if row["kind"] == "mirror_transfer"
    )
    assert plan["paths"] == ["app.py"]
    assert plan["excluded_artifacts"] == 2
    assert transfer["transfer_mode"] == "source_only_archive"
    transfer_commands = [command for command, _env in environment.commands]
    assert not any("/tmp/gt-source-paths.nul" in command for command in transfer_commands)
    assert not any("/tmp/gt-source-mirror.tar.gz" in command for command in transfer_commands)
    assert any(
        "rmdir -- /tmp/.gt-mirror." in command and "test ! -e /tmp/.gt-mirror." in command
        for command in transfer_commands
    )
    assert receipt["metrics"]["repository_mirror_files"] == 1
    assert receipt["host_execution"]["category_counts"]["repository_transfer"] >= 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cwd", "expected_member", "expected_transform"),
    [
        ("/workspace", "workspace/pkg/app.py", "s,^workspace/, ,"),
        ("/app/dclm", "app/dclm/pkg/app.py", "s,^app/dclm/, ,"),
    ],
)
async def test_source_archive_is_rooted_at_resolved_task_cwd(
    tmp_path, cwd, expected_member, expected_transform
):
    """A source mirror must not assume that every task lives at /app."""

    class CaptureEnvironment(_Environment):
        async def download_file(self, source_path, target_path):
            payload = b"def solve():\n    return 1\n"
            with tarfile.open(target_path, "w:gz") as archive:
                member = tarfile.TarInfo("pkg/app.py")
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))

    plan = SourceMirrorPlan(
        paths=("pkg/app.py",),
        total_bytes=25,
        source_files=1,
        metadata_files=0,
        excluded_artifacts=0,
        excluded_deliverables=0,
        excluded_oversize=0,
        excluded_source_oversize=0,
        excluded_budget=0,
        excluded_source_budget=0,
        manifest_sha256="m",
        complete=True,
        reason_codes=(),
    )
    environment = CaptureEnvironment()
    session = RepositorySession.temporary(instruction="Fix pkg/app.py")
    agent = MiniSweCentralAgent(logs_dir=tmp_path, model_name="test", cwd=cwd)

    try:
        await agent._transfer_source_archive(
            environment,
            session,
            plan,
            source_revision="s1",
        )
    finally:
        session.close()

    commands = [command for command, _env in environment.commands]
    append = next(command for command in commands if "base64 -d >>" in command)
    encoded = append.split("printf '%s' '", 1)[1].split("' | base64", 1)[0]
    assert base64.b64decode(encoded).decode("utf-8") == expected_member + "\0"
    archive_command = next(command for command in commands if command.startswith("tar "))
    assert expected_transform.replace(" ", "") in archive_command
    assert "--transform='s,^app/,,'" not in archive_command


@pytest.mark.asyncio
async def test_strict_graph_gate_allows_current_certified_graph(tmp_path):
    class TransferEnvironment(_Environment):
        async def download_dir_with_exclusions(self, *, source_dir, target_dir, exclude):
            root = Path(target_dir)
            (root / "app.py").write_text(
                "def target(value):\n    return value + 1\n\ndef caller():\n    return target(1)\n",
                encoding="utf-8",
            )

    model = _ScriptedModel(["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"])
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        enable_context_frontier=True,
        require_graph_ready=True,
    )
    agent._model_factory = lambda: model

    await agent.run(
        "Change target to return the requested value.",
        TransferEnvironment(),
        AgentContext(),
    )

    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    assert len(model.observed_history) == 1
    assert receipt["metrics"]["repository_graph_gate_enabled"] == 1
    assert receipt["metrics"]["repository_graph_gate_blocked"] == 0
    assert receipt["metrics"]["repository_graph_source_revision"]


@pytest.mark.asyncio
async def test_frontier_fact_is_one_shot_and_task_budget_is_receipted(tmp_path):
    class TransferEnvironment(_Environment):
        async def download_dir_with_exclusions(self, *, source_dir, target_dir, exclude):
            root = Path(target_dir)
            (root / "src").mkdir(parents=True)
            (root / "src" / "greeter.py").write_text(
                "def greet(name: str) -> str:\n    return f'hello {name}'\n"
            )

    model = _ScriptedModel(["cat src/greeter.py", "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"])
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        enable_context_frontier=True,
        context_frontier_task_budget_chars=400,
    )
    agent._model_factory = lambda: model

    await agent.run(
        "Ensure greet returns an uppercase greeting.",
        TransferEnvironment(),
        AgentContext(),
    )

    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    deliveries = receipt["repository_intelligence"]["frontier_deliveries"]
    delivered_ids = [fact for row in deliveries for fact in row["fact_ids"]]
    assert len(delivered_ids) == len(set(delivered_ids))
    assert receipt["metrics"]["context_frontier_duplicate_facts"] == 0
    assert receipt["metrics"]["context_frontier_chars_added"] <= 400
    assert receipt["metrics"]["context_frontier_task_budget_chars"] == 400


@pytest.mark.asyncio
async def test_proven_read_only_action_reuses_workspace_snapshot_without_rescan(tmp_path):
    model = _ScriptedModel(["cat src/greeter.py", "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"])
    environment = _Environment()
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        enable_repository_intelligence=False,
    )
    agent._model_factory = lambda: model

    await agent.run("Inspect the file and finish.", environment, AgentContext())

    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    manifest_execs = [
        row
        for row in receipt["host_execution"]["receipts"]
        if row["category"] == "workspace_manifest" and not row["cache_hit"]
    ]
    manifest_cache_hits = [
        row
        for row in receipt["host_execution"]["receipts"]
        if row["category"] == "workspace_manifest" and row["cache_hit"]
    ]
    assert len(manifest_execs) == 2  # initial snapshot plus submit postflight
    assert len(manifest_cache_hits) == 1
    assert manifest_cache_hits[0]["action_id"] == 1


@pytest.mark.asyncio
async def test_partial_completion_plan_executes_no_private_predicates(tmp_path):
    model = _ScriptedModel(
        [
            "touch /app/task_file/output_data/plan_b1.jsonl",
            "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",
        ]
    )
    environment = _Environment()
    agent = MiniSweCentralAgent(logs_dir=tmp_path, model_name="test")
    agent._model_factory = lambda: model

    await agent.run(
        "Produce /app/task_file/output_data/plan_b1.jsonl containing exactly 3 rows "
        "and satisfy all scheduling constraints.",
        environment,
        AgentContext(),
    )

    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    assert receipt["metrics"]["completion_plan_status"] == "partial"
    assert receipt["metrics"]["completion_probe_execs"] == 0
    assert not any(command.startswith("test -s ") for command, _env in environment.commands)


@pytest.mark.asyncio
async def test_custom_probe_failure_is_not_reframed_as_model_guidance(tmp_path):
    class ProbeEnvironment(_Environment):
        async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
            self.commands.append((command, env))
            if command.startswith("uname "):
                return ExecResult(stdout="Linux\t6.8\tversion\tx86_64\n", return_code=0)
            if "-printf" in command:
                return ExecResult(stdout="", return_code=0)
            if command == "python3 /tmp/test_single.py":
                return ExecResult(
                    stdout="UnexpectedAlertPresentException: exploit alert fired\n",
                    return_code=1,
                )
            return ExecResult(return_code=0)

    model = _ScriptedModel(
        ["python3 /tmp/test_single.py", "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"]
    )
    agent = MiniSweCentralAgent(logs_dir=tmp_path, model_name="test")
    agent._model_factory = lambda: model

    await agent.run("Demonstrate the browser exploit behavior.", ProbeEnvironment(), AgentContext())

    assert len(model.observed_history) == 2
    second_request = "\n".join(model.observed_history[1])
    assert "UnexpectedAlertPresentException" in second_request
    assert "Validation failed for the current source revision" not in second_request
    assert "failing required check" not in second_request
    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    assert receipt["metrics"]["payload_deliveries"] == 0
    assert receipt["features"]["required_check_claims_without_declared_id"] == 0


@pytest.mark.asyncio
async def test_context_transform_preserves_oversized_read_before_budget_pressure(tmp_path):
    class LargeReadEnvironment(_Environment):
        async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
            self.commands.append((command, env))
            if command.startswith("uname "):
                return ExecResult(stdout="Linux\t6.8\tversion\tx86_64\n", return_code=0)
            if "-printf" in command:
                return ExecResult(stdout="", return_code=0)
            if command == "cat huge.log":
                return ExecResult(stdout="Z" * 30_000, return_code=0)
            return ExecResult(return_code=0)

    model = _ScriptedModel(["cat huge.log", "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"])
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        enable_context_compaction=True,
    )
    agent._model_factory = lambda: model

    await agent.run("Inspect huge.log and finish.", LargeReadEnvironment(), AgentContext())

    second_request = "\n".join(model.observed_history[1])
    assert "Z" * 30_000 in second_request
    assert "Tool output bounded by host" not in second_request
    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    assert receipt["metrics"]["context_compactions"] == 0
    compiler = receipt["model_call_contexts"][1]["context_compiler"]
    assert compiler["bounded_observation_count"] == 0
    call = receipt["model_call_contexts"][1]
    assert call["stock_provider_messages_sha256"] == call["provider_messages_sha256"]
    assert call["provider_changed_message_indices"] == []
    assert all(
        not row["provider_compaction_epoch_started"] for row in receipt["model_call_contexts"]
    )


@pytest.mark.asyncio
async def test_context_soft_character_limit_starts_repeated_bounded_compaction_epochs(tmp_path):
    class LargeReadEnvironment(_Environment):
        async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
            self.commands.append((command, env))
            if command.startswith("uname "):
                return ExecResult(stdout="Linux\t6.8\tversion\tx86_64\n", return_code=0)
            if "-printf" in command:
                return ExecResult(stdout="", return_code=0)
            if command.startswith("cat huge"):
                return ExecResult(stdout=command[-5:] * 5_000, return_code=0)
            return ExecResult(return_code=0)

    model = _ScriptedModel(
        [
            "cat huge1.log",
            "cat huge2.log",
            "cat huge3.log",
            "cat huge4.log",
            "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",
        ]
    )
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        enable_context_compaction=True,
        context_trigger_chars=20_000,
        context_target_chars=12_000,
        context_min_compaction_savings_chars=1,
        context_min_compaction_savings_ratio=0.0,
    )
    agent._model_factory = lambda: model

    await agent.run("Inspect the logs and finish.", LargeReadEnvironment(), AgentContext())

    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    assert receipt["metrics"]["context_compactions"] >= 2
    assert len(receipt["metrics"]["context_compaction_epochs"]) >= 2
    assert all(
        row["trigger_kind"] == "character_pressure"
        for row in receipt["metrics"]["context_compaction_epochs"]
    )
    assert receipt["metrics"]["context_unique_reasoning_chars_removed"] == 0
    assert receipt["metrics"]["context_chars_elided"] > 0
    assert all(
        row["provider_change_reason"] != "none"
        for row in receipt["model_call_contexts"]
        if row["provider_view_changed"]
    )
    view_chars = [
        int(row["final_provider_chars"])
        for row in receipt["model_call_contexts"]
        if row["call"] >= 4
    ]
    assert view_chars and max(view_chars) < 40_000
    assert int(
        receipt["metrics"]["context_compaction_epochs"][-1]["epoch"]
    ) == receipt["metrics"]["context_compactions"]


@pytest.mark.asyncio
async def test_relational_v2_ignores_soft_character_trigger_until_provider_pressure(
    tmp_path,
):
    class LargeReadEnvironment(_Environment):
        async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
            self.commands.append((command, env))
            if command.startswith("uname "):
                return ExecResult(stdout="Linux\t6.8\tversion\tx86_64\n", return_code=0)
            if "-printf" in command:
                return ExecResult(stdout="", return_code=0)
            if command.startswith("cat huge"):
                return ExecResult(stdout=command[-5:] * 5_000, return_code=0)
            return ExecResult(return_code=0)

    model = _ScriptedModel(
        [
            "cat huge1.log",
            "cat huge2.log",
            "cat huge3.log",
            "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",
        ]
    )
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        treatment_profile="central_relational_v2",
        enable_context_compaction=True,
        context_trigger_chars=1_500,
        context_target_chars=800,
        context_min_compaction_savings_chars=1,
        context_min_compaction_savings_ratio=0.0,
    )
    agent._model_factory = lambda: model

    await agent.run("Inspect the logs and finish.", LargeReadEnvironment(), AgentContext())

    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    assert receipt["metrics"]["context_compactions"] == 0
    assert receipt["metrics"]["context_compaction_deferrals"] == []
    assert all(
        row["trigger_kind"] != "character_pressure"
        for row in receipt["metrics"]["context_compaction_epochs"]
    )


@pytest.mark.asyncio
async def test_soft_compaction_defers_when_cache_break_savings_are_too_small(tmp_path):
    class LargeReadEnvironment(_Environment):
        async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
            self.commands.append((command, env))
            if command.startswith("uname "):
                return ExecResult(stdout="Linux\t6.8\tversion\tx86_64\n", return_code=0)
            if "-printf" in command:
                return ExecResult(stdout="", return_code=0)
            if command.startswith("cat huge"):
                return ExecResult(stdout=command[-5:] * 5_000, return_code=0)
            return ExecResult(return_code=0)

    model = _ScriptedModel(
        [
            "cat huge1.log",
            "cat huge2.log",
            "cat huge3.log",
            "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",
        ]
    )
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        enable_context_compaction=True,
        context_trigger_chars=20_000,
        context_target_chars=12_000,
    )
    agent._model_factory = lambda: model

    await agent.run("Inspect the logs and finish.", LargeReadEnvironment(), AgentContext())

    metrics = json.loads((tmp_path / "central_receipt.json").read_text())["metrics"]
    assert metrics["context_compactions"] == 0
    assert metrics["context_compaction_deferral_count"] >= 1


@pytest.mark.asyncio
async def test_reasoning_dominated_pressure_is_reported_without_deleting_reasoning(
    tmp_path,
):
    class ReasoningHeavyModel(_ScriptedModel):
        def __init__(self):
            super().__init__(
                [
                    "echo one",
                    "echo two",
                    "echo three",
                    "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",
                ]
            )
            self.response_index = 0

        def query(self, messages):
            response = super().query(messages)
            self.response_index += 1
            response["content"] = (
                f"distinct hypothesis {self.response_index}: " + "R" * 4_000
            )
            response["reasoning_content"] = (
                f"private chain {self.response_index}: " + "Q" * 4_000
            )
            return response

    model = ReasoningHeavyModel()
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        enable_context_compaction=True,
        context_trigger_chars=1_500,
        context_target_chars=800,
    )
    agent._model_factory = lambda: model

    await agent.run("Inspect the state and finish.", _Environment(), AgentContext())

    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    deferrals = receipt["metrics"]["context_compaction_deferrals"]
    assert deferrals
    assert any(
        row["reason"] == "distinct_assistant_reasoning_preservation_boundary"
        for row in deferrals
    )
    assert receipt["metrics"]["context_unique_reasoning_chars_removed"] == 0


@pytest.mark.asyncio
async def test_disabled_task_start_advisory_never_leaks_into_call_two(tmp_path):
    class TransferEnvironment(_Environment):
        async def download_dir_with_exclusions(self, *, source_dir, target_dir, exclude):
            root = Path(target_dir)
            (root / "src").mkdir(parents=True)
            (root / "pyproject.toml").write_text("[project]\nname='demo'\n")
            (root / "src" / "greeter.py").write_text("def greet(): return 'hello'\n")

    model = _ScriptedModel(["echo inspect", "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"])
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        enable_task_start_advisory=False,
    )
    agent._model_factory = lambda: model

    await agent.run("Change the greeting.", TransferEnvironment(), AgentContext())

    assert len(model.observed_history) == 2
    assert not any(
        "src/greeter.py" in item for history in model.observed_history for item in history
    )
    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    assert not any(row["feature_id"] == "GT_LOC_RESLOT" for row in receipt["guidance_deliveries"])


@pytest.mark.asyncio
async def test_receipt_hashes_the_provider_prepared_messages_not_private_extra(tmp_path):
    class PreparedModel(_ScriptedModel):
        model_kwargs = {"temperature": 1.0}
        tools = [{"type": "function", "function": {"name": "bash"}}]

        def __init__(self, commands):
            super().__init__(commands)
            self.raw_history = []

        def _prepare_messages_for_api(self, messages):
            return [
                {key: value for key, value in message.items() if key != "extra"}
                for message in messages
            ]

        def query(self, messages):
            self.raw_history.append(json.loads(json.dumps(messages)))
            return super().query(messages)

    model = PreparedModel(["echo inspect", "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"])
    agent = MiniSweCentralAgent(logs_dir=tmp_path, model_name="test")
    agent._model_factory = lambda: model

    await agent.run("Inspect café, then finish.", _Environment(), AgentContext())

    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    row = receipt["model_call_contexts"][1]
    logical = model.observed_history[1]
    prepared = model._prepare_messages_for_api(model.raw_history[1])
    expected = hashlib.sha256(
        json.dumps(
            prepared,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    assert row["provider_messages_sha256"] == expected
    assert row["provider_request_chars"] == len(
        json.dumps(
            prepared,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    )
    assert row["provider_message_count"] == len(prepared)
    assert logical


@pytest.mark.asyncio
async def test_provider_query_marker_proves_whether_a_paid_call_started(monkeypatch, tmp_path):
    monkeypatch.setenv("GT_COMMIT", "a" * 40)
    monkeypatch.setenv("OPENROUTER_API_KEY", "must-not-be-persisted")
    model = _ScriptedModel(["echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"])
    agent = MiniSweCentralAgent(logs_dir=tmp_path, model_name="test")
    agent._model_factory = lambda: model

    await agent.run("Finish.", _Environment(), AgentContext())

    raw = (tmp_path / "provider_query_started.json").read_text(encoding="utf-8")
    marker = json.loads(raw)
    assert marker["schema"] == "gt.provider_query_started.v1"
    assert marker["calls_started"] == 1
    assert marker["bootstrap_calls_started"] == 0
    assert marker["executor_calls_started"] == 1
    assert marker["last_call_kind"] == "executor"
    assert marker["gt_commit"] == "a" * 40
    assert len(marker["request_payload_sha256"]) == 64
    assert "must-not-be-persisted" not in raw


@pytest.mark.asyncio
async def test_executor_marker_failure_prevents_provider_transport(tmp_path):
    class MustNotQuery(_ScriptedModel):
        def __init__(self):
            super().__init__(["echo never-executed"])
            self.queries = 0

        def query(self, messages, **kwargs):
            self.queries += 1
            raise AssertionError("provider transport must not start without a durable marker")

    model = MustNotQuery()
    agent = MiniSweCentralAgent(logs_dir=tmp_path, model_name="test")
    agent._model_factory = lambda: model
    agent._write_provider_query_marker = lambda **kwargs: "OSError"  # type: ignore[method-assign]
    context = AgentContext()

    await agent.run("Finish.", _Environment(), context)

    receipt = json.loads((tmp_path / "central_receipt.json").read_text(encoding="utf-8"))
    assert model.queries == 0
    assert receipt["metrics"]["model_query_invocations"] == 0
    assert receipt["metrics"]["provider_query_marker_error"] == "OSError"
    assert context.metadata["exit_status"] == "ProviderQueryMarkerError"


@pytest.mark.asyncio
async def test_marker_failure_does_not_consume_or_receipt_pending_visible_evidence(tmp_path):
    class FailureEnvironment(_Environment):
        async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
            self.commands.append((command, env))
            if command.startswith("uname "):
                return ExecResult(stdout="Linux\t6.8\tversion\tx86_64\n", return_code=0)
            if "-printf" in command:
                return ExecResult(stdout="", return_code=0)
            if command == "pytest -q":
                return ExecResult(stdout="tests/test_app.py::test_app FAILED\n", return_code=1)
            raise AssertionError(command)

    model = _ScriptedModel(
        ["pytest -q", "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"]
    )
    agent = MiniSweCentralAgent(logs_dir=tmp_path, model_name="test")
    agent._model_factory = lambda: model
    feedback_calls = 0

    def forced_feedback(*args, **kwargs):
        nonlocal feedback_calls
        feedback_calls += 1
        if feedback_calls == 1:
            agent._features._prepared_guidance = {  # type: ignore[attr-defined]
                "feedback": "Validation failed for pytest -q: one grounded failure.",
                "feature_id": "covering_red",
                "delivery_id": "forced-grounded-delivery",
                "effect_ids": [],
                "claim_ids": ["forced-grounded-claim"],
                "claim_anchors": [{"command": "pytest -q"}],
                "evidence_action": 1,
                "evidence_actions": [1],
                "revision": "source-0",
            }
            return str(agent._features._prepared_guidance["feedback"])  # type: ignore[attr-defined]
        return ""

    agent._features.model_feedback = forced_feedback  # type: ignore[method-assign]
    marker_calls = 0

    def marker(**kwargs):
        nonlocal marker_calls
        marker_calls += 1
        return "" if marker_calls == 1 else "OSError"

    agent._write_provider_query_marker = marker  # type: ignore[method-assign]

    await agent.run("Run pytest before finishing.", FailureEnvironment(), AgentContext())

    receipt = json.loads((tmp_path / "central_receipt.json").read_text(encoding="utf-8"))
    rows, _failures, totals = audit_provider_deliveries(receipt)
    assert len(model.observed_history) == 1
    assert receipt["guidance_deliveries"] == []
    assert rows == []
    assert totals["delivery_count"] == 0


@pytest.mark.asyncio
async def test_preflight_spy_runs_before_selected_command_executes(tmp_path):
    events = []

    class OrderedEnvironment(_Environment):
        async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
            if command == "cat src/app.py":
                events.append("exec")
            return await super().exec(command, cwd, env, timeout_sec, user)

    model = _ScriptedModel(["cat src/app.py", "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"])
    agent = MiniSweCentralAgent(logs_dir=tmp_path, model_name="test", enable_preflight=True)
    agent._model_factory = lambda: model
    original = agent._features.preflight_action

    def ordered_preflight(*args, **kwargs):
        events.append("preflight")
        return original(*args, **kwargs)

    agent._features.preflight_action = ordered_preflight
    await agent.run("Read app.py.", OrderedEnvironment(), AgentContext())

    assert events[:2] == ["preflight", "exec"]


@pytest.mark.asyncio
async def test_material_edit_preflight_returns_then_revised_edit_executes(tmp_path):
    class RevisionEnvironment(_Environment):
        def __init__(self):
            super().__init__()
            self.edited = False
            self.executed_edits = []

        async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
            self.commands.append((command, env))
            if command.startswith("uname "):
                return ExecResult(stdout="Linux\t6.8\tversion\tx86_64\n", return_code=0)
            if "-printf" in command:
                size = 4 if self.edited else 3
                stamp = "2.0" if self.edited else "1.0"
                return ExecResult(stdout=f"f\t{size}\t{stamp}\t{stamp}\tapp.py\t\n", return_code=0)
            if command.startswith("sha256sum"):
                return ExecResult(
                    stdout=("b" if self.edited else "a") * 64 + "  app.py\n",
                    return_code=0,
                )
            if command.startswith("python3 -c"):
                return ExecResult(stdout='{"app.py":"eCA9IDEK"}\n', return_code=0)
            if command.startswith("sed -i"):
                self.executed_edits.append(command)
                self.edited = True
                return ExecResult(stdout="", return_code=0)
            if "COMPLETE_TASK" in command:
                return ExecResult(stdout="COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n", return_code=0)
            return ExecResult(stdout="", return_code=0)

    environment = RevisionEnvironment()
    first = "sed -i 's/x/y/' app.py"
    revised = "sed -i 's/x/z/' app.py"
    model = _ScriptedModel([first, revised, "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"])
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        enable_preflight=True,
        enable_lint=False,
    )
    agent._model_factory = lambda: model
    real_preflight = agent._features.preflight_action
    returned = False

    def material_once(proposed, *args, **kwargs):
        nonlocal returned
        if not returned and proposed.raw_command == first:
            returned = True
            return PreflightDecision(
                ActionDisposition.RETURN_TO_MODEL,
                proposed.raw_command,
                evidence=("Exact target has a material coupled-file risk.",),
                reason_codes=("material_edit_risk",),
                confidence=1.0,
                source_revision=proposed.source_revision,
            )
        return real_preflight(proposed, *args, **kwargs)

    agent._features.preflight_action = material_once
    await agent.run("Change app.py.", environment, AgentContext())

    assert environment.executed_edits == [revised]
    assert any("material coupled-file risk" in item for item in model.observed_history[1])
    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    assert receipt["features"]["action_metrics"]["workspace_change_actions"] == 1


@pytest.mark.asyncio
async def test_missing_edit_passes_to_shell_then_postflight_keeps_loop_live(tmp_path):
    class ExistingFileEnvironment(_Environment):
        def __init__(self):
            super().__init__()
            self.executed_edits: list[str] = []
            self.edited = False

        async def download_dir_with_exclusions(self, *, source_dir, target_dir, exclude):
            Path(target_dir, "app.py").write_text("def x():\n    return 1\n")

        async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
            self.commands.append((command, env))
            if command.startswith("uname "):
                return ExecResult(stdout="Linux\t6.8\tversion\tx86_64\n", return_code=0)
            if "-printf" in command:
                size = 22
                stamp = "2.0" if self.edited else "1.0"
                return ExecResult(
                    stdout=f"f\t{size}\t{stamp}\t{stamp}\tapp.py\t\n",
                    return_code=0,
                )
            if command.startswith("sha256sum"):
                digest = "b" if self.edited else "a"
                return ExecResult(stdout=(digest * 64) + "  app.py\n", return_code=0)
            if command.startswith("python3 -c"):
                encoded = (
                    "ZGVmIHkoKToKICAgIHJldHVybiAyCg=="
                    if self.edited
                    else "ZGVmIHgoKToKICAgIHJldHVybiAxCg=="
                )
                return ExecResult(stdout=f'{{"app.py":"{encoded}"}}\n', return_code=0)
            if command.startswith("sed -i"):
                self.executed_edits.append(command)
                self.edited = True
                return ExecResult(return_code=0)
            if "COMPLETE_TASK" in command:
                return ExecResult(stdout="COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n", return_code=0)
            return ExecResult(return_code=0)

    first = "sed -i 's/x/y/' missing.py"
    revised = "sed -i 's/x/y/' app.py"
    model = _ScriptedModel([first, revised, "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"])
    environment = ExistingFileEnvironment()
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        preflight_mode=PreflightMode.ASSISTIVE_SAFE,
        enable_lint=False,
    )
    agent._model_factory = lambda: model

    await agent.run("Edit app.py.", environment, AgentContext())

    assert environment.executed_edits == [first, revised]
    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    cycles = receipt["features"]["action_cycles"]
    first_cycle = next(row for row in cycles if row["proposed"]["raw_command"] == first)
    assert first_cycle["candidate_decision"]["disposition"] == "pass"
    assert first_cycle["applied_disposition"] == "pass"
    assert first_cycle["executed"] is True
    revised_cycle = next(row for row in cycles if row["proposed"]["raw_command"] == revised)
    assert revised_cycle["executed"] is True
    assert revised_cycle["postflight"]["source_revision"]
    session = receipt["repository_session"]
    assert session["fresh"] is True
    assert len(session["refresh_log"]) == 3
    assert [row["mode"] for row in session["refresh_log"]] == [
        "full",
        "incremental",
        "action_query",
    ]
    assert session["refresh_log"][-1]["active_paths"] == ["app.py"]
    assert session["source_revision"] == receipt["source_revision"]
    assert receipt["metrics"]["repository_incremental_refreshes"] == 1
    assert receipt["metrics"]["preflight_commands_returned_to_model"] == 0
    assert receipt["metrics"]["preflight_commands_changed_after_return"] == 0


@pytest.mark.asyncio
async def test_shadow_records_material_candidate_but_executes_original(tmp_path):
    model = _ScriptedModel(
        [
            "sed -i 's/x/y/' missing.py",
            "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",
        ]
    )
    environment = _Environment()
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        preflight_mode=PreflightMode.SHADOW,
        enable_lint=False,
    )
    agent._model_factory = lambda: model

    await agent.run("Edit a file.", environment, AgentContext())

    assert any(command.startswith("sed -i") for command, _ in environment.commands)
    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    cycle = next(
        row
        for row in receipt["features"]["action_cycles"]
        if row["proposed"]["raw_command"].startswith("sed -i")
    )
    assert cycle["candidate_decision"]["disposition"] == "pass"
    assert cycle["applied_disposition"] == "pass"
    assert cycle["executed"] is True


@pytest.mark.asyncio
async def test_off_and_shadow_dispatch_identical_model_commands(tmp_path):
    commands = ["cat app.py", "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"]
    off_model = _ScriptedModel(commands)
    shadow_model = _ScriptedModel(commands)
    off_environment = _Environment()
    shadow_environment = _Environment()
    off = MiniSweCentralAgent(
        logs_dir=tmp_path / "off",
        model_name="test",
        preflight_mode=PreflightMode.OFF,
        integration_mode=GTIntegrationMode.OFF,
    )
    shadow = MiniSweCentralAgent(
        logs_dir=tmp_path / "shadow",
        model_name="test",
        preflight_mode=PreflightMode.SHADOW,
        integration_mode=GTIntegrationMode.AUDIT,
    )
    off._model_factory = lambda: off_model
    shadow._model_factory = lambda: shadow_model

    await off.run("Read app.py.", off_environment, AgentContext())
    await shadow.run("Read app.py.", shadow_environment, AgentContext())

    off_selected = [
        command
        for command, _ in off_environment.commands
        if "-printf" not in command and not command.startswith("uname ")
    ]
    shadow_selected = [
        command
        for command, _ in shadow_environment.commands
        if "-printf" not in command and not command.startswith("uname ")
    ]
    assert shadow_selected == off_selected
    off_receipt = json.loads((tmp_path / "off" / "central_receipt.json").read_text())
    shadow_receipt = json.loads((tmp_path / "shadow" / "central_receipt.json").read_text())
    assert off_receipt["features"]["action_cycles"] == []
    assert len(shadow_receipt["features"]["action_cycles"]) == 2
    assert off_receipt["integration_mode"] == "off"
    assert shadow_receipt["integration_mode"] == "audit"
    assert [row["provider_messages_sha256"] for row in off_receipt["model_call_contexts"]] == [
        row["provider_messages_sha256"] for row in shadow_receipt["model_call_contexts"]
    ]


def test_integration_mode_is_one_switch_and_audit_cannot_intervene(tmp_path):
    off = MiniSweCentralAgent(
        logs_dir=tmp_path / "off",
        model_name="test",
        integration_mode="off",
        preflight_mode="assistive_safe",
        enable_context_compaction=True,
        enable_task_start_advisory=True,
    )
    audit = MiniSweCentralAgent(
        logs_dir=tmp_path / "audit",
        model_name="test",
        integration_mode="audit",
        preflight_mode="assistive_safe",
        enable_context_compaction=True,
        enable_task_start_advisory=True,
    )

    assert off.integration_mode is GTIntegrationMode.OFF
    assert off.preflight_mode is PreflightMode.OFF
    assert off.enable_context_compaction is False
    assert off.enable_task_start_advisory is False
    assert off.enable_lint is False
    assert off.enable_submit_readiness is False
    assert audit.integration_mode is GTIntegrationMode.AUDIT
    assert audit.preflight_mode is PreflightMode.SHADOW
    assert audit.enable_context_compaction is False
    assert audit.enable_task_start_advisory is False


def test_certified_shadow_is_provider_neutral_and_cannot_run_active_controllers(tmp_path):
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        policy_mode="certified_shadow",
        preflight_mode="assistive_safe",
        enable_context_compaction=True,
        enable_completion_controller=True,
        enable_adaptive_validation_timeout=True,
    )

    assert agent.policy_mode is GTPolicyMode.CERTIFIED_SHADOW
    assert agent.preflight_mode is PreflightMode.SHADOW
    assert agent.enable_context_compaction is False
    assert agent.enable_completion_controller is False
    assert agent.enable_feature_guidance is False
    assert agent.enable_adaptive_validation_timeout is False
    assert agent._features.model_visible is False


@pytest.mark.asyncio
async def test_preflight_timeout_is_recorded_and_fails_open(tmp_path):
    model = _ScriptedModel(["cat app.py", "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"])
    environment = _Environment()
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        preflight_mode=PreflightMode.ASSISTIVE_SAFE,
        preflight_timeout_sec=0.001,
    )
    agent._model_factory = lambda: model

    def slow_preflight(*args, **kwargs):
        time.sleep(0.03)
        raise AssertionError("result should be ignored after timeout")

    agent._features.preflight_action = slow_preflight
    await agent.run("Read app.py.", environment, AgentContext())

    assert any(command == "cat app.py" for command, _ in environment.commands)
    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    cycle = next(
        row
        for row in receipt["features"]["action_cycles"]
        if row["proposed"]["raw_command"] == "cat app.py"
    )
    assert "preflight_timeout" in cycle["candidate_decision"]["reason_codes"]
    assert cycle["applied_disposition"] == "pass"
    assert cycle["executed"] is True


@pytest.mark.asyncio
async def test_rewrite_is_never_dispatched_in_assistive_safe_mode(tmp_path):
    original = "cat app.py"
    rewritten = "rm app.py"
    model = _ScriptedModel([original, "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"])
    environment = _Environment()
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        preflight_mode=PreflightMode.ASSISTIVE_SAFE,
    )
    agent._model_factory = lambda: model
    real_preflight = agent._features.preflight_action

    def unsafe_rewrite(proposed, *args, **kwargs):
        if proposed.raw_command != original:
            return real_preflight(proposed, *args, **kwargs)
        return PreflightDecision(
            ActionDisposition.REWRITE,
            rewritten,
            evidence=("claimed equivalent",),
            reason_codes=("rewrite_candidate",),
            confidence=1.0,
            source_revision=proposed.source_revision,
        )

    agent._features.preflight_action = unsafe_rewrite
    await agent.run("Read app.py.", environment, AgentContext())

    commands = [command for command, _ in environment.commands]
    assert original in commands
    assert rewritten not in commands
    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    cycle = next(
        row
        for row in receipt["features"]["action_cycles"]
        if row["proposed"]["raw_command"] == original
    )
    assert "rewrite_disabled" in cycle["applied_reason_codes"]


@pytest.mark.asyncio
async def test_assistive_safe_keeps_read_only_batch_and_pairs_every_output(tmp_path):
    submit = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
    model = _BatchModel([["cat a.py", "rg -n x src"], [submit]])
    environment = _Environment()
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        preflight_mode=PreflightMode.ASSISTIVE_SAFE,
    )
    agent._model_factory = lambda: model

    await agent.run("Inspect the files.", environment, AgentContext())

    commands = [command for command, _ in environment.commands]
    assert "cat a.py" in commands
    assert "rg -n x src" in commands
    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    assert receipt["metrics"]["stale_batched_actions_prevented"] == 0


@pytest.mark.asyncio
async def test_successful_unknown_without_material_change_does_not_split_batch(tmp_path):
    submit = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
    model = _BatchModel([["pwd", "cat a.py"], [submit]])
    environment = _Environment()
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        preflight_mode=PreflightMode.ASSISTIVE_SAFE,
    )
    agent._model_factory = lambda: model

    await agent.run("Inspect the workspace.", environment, AgentContext())

    commands = [command for command, _ in environment.commands]
    assert "pwd" in commands
    assert "cat a.py" in commands
    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    assert receipt["metrics"]["stale_batched_actions_prevented"] == 0


@pytest.mark.asyncio
async def test_unclassified_exploration_failure_alone_does_not_split_batch(tmp_path):
    class ExplorationEnvironment(_Environment):
        async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
            self.commands.append((command, env))
            if command.startswith("uname "):
                return ExecResult(stdout="Linux\t6.8\tversion\tx86_64\n", return_code=0)
            if "-printf" in command:
                return ExecResult(stdout="", return_code=0)
            if command == "ls missing":
                return ExecResult(stderr="not found\n", return_code=1)
            return ExecResult(return_code=0)

    submit = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
    model = _BatchModel([["ls missing", "cat a.py"], [submit]])
    environment = ExplorationEnvironment()
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        preflight_mode=PreflightMode.ASSISTIVE_SAFE,
    )
    agent._model_factory = lambda: model

    await agent.run("Inspect the workspace.", environment, AgentContext())

    commands = [command for command, _ in environment.commands]
    assert "ls missing" in commands
    assert "cat a.py" in commands
    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    assert receipt["metrics"]["stale_batched_actions_prevented"] == 0


@pytest.mark.asyncio
async def test_assistive_safe_preserves_model_selected_ordered_mutation_batch(tmp_path):
    submit = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
    model = _BatchModel([["touch app.py", "rm app.py"], [submit], [submit]])
    environment = _ObservedMutationEnvironment("touch app.py", "f\t6\t2.0\t2.0\tapp.py\t\n")
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        preflight_mode=PreflightMode.ASSISTIVE_SAFE,
        enable_lint=False,
    )
    agent._model_factory = lambda: model

    await agent.run("Create app.py matching the reference layout.", environment, AgentContext())

    commands = [command for command, _ in environment.commands]
    assert "touch app.py" in commands
    assert "rm app.py" in commands
    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    assert receipt["metrics"]["stale_batched_actions_prevented"] == 0
    executed = next(
        row
        for row in receipt["features"]["action_cycles"]
        if row["proposed"]["raw_command"] == "rm app.py"
    )
    assert executed["executed"] is True


@pytest.mark.asyncio
async def test_compound_mutating_action_preserves_following_read(tmp_path):
    submit = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
    model = _BatchModel([["mkdir -p work && echo made", "cat work/result"], [submit], [submit]])
    environment = _ObservedMutationEnvironment(
        "mkdir -p work && echo made", "d\t0\t2.0\t2.0\twork\t\n"
    )
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        preflight_mode=PreflightMode.ASSISTIVE_SAFE,
    )
    agent._model_factory = lambda: model

    await agent.run(
        "Create the work directory containing the expected layout.", environment, AgentContext()
    )

    commands = [command for command, _ in environment.commands]
    assert "mkdir -p work && echo made" in commands
    assert "cat work/result" in commands
    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    assert receipt["metrics"]["stale_batched_actions_prevented"] == 0


@pytest.mark.asyncio
async def test_terminal_submit_pairs_and_cancels_predecided_suffix(tmp_path):
    submit = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
    model = _BatchModel([[submit, "rm app.py"]])
    environment = _Environment()
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        preflight_mode=PreflightMode.ASSISTIVE_SAFE,
    )
    agent._model_factory = lambda: model

    await agent.run("Finish the task.", environment, AgentContext())

    commands = [command for command, _ in environment.commands]
    assert submit in commands
    assert "rm app.py" not in commands
    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    cancelled = next(
        row
        for row in receipt["features"]["action_cycles"]
        if row["proposed"]["raw_command"] == "rm app.py"
    )
    assert cancelled["postflight"]["reason"] == "terminal_submit"


def test_central_agent_is_host_owned_not_installed():
    assert issubclass(MiniSweCentralAgent, BaseAgent)
    assert not issubclass(MiniSweCentralAgent, BaseInstalledAgent)
    assert inspect.iscoroutinefunction(MiniSweCentralAgent.run)
    assert MiniSweCentralAgent.SUPPORTS_ATIF is True


@pytest.mark.asyncio
async def test_setup_does_not_install_or_upload_anything(tmp_path):
    agent = MiniSweCentralAgent(logs_dir=tmp_path, model_name="test-model")
    environment = _Environment()

    await agent.setup(environment)

    assert environment.commands == []


def test_shadow_and_treatment_are_both_central_gt_on_arms(tmp_path):
    treatment = MiniSweCentralAgent(logs_dir=tmp_path / "a", model_name="test")
    shadow = MiniSweCentralShadowAgent(logs_dir=tmp_path / "b", model_name="test")

    assert treatment.runtime_mode == "treatment"
    assert shadow.runtime_mode == "shadow"
    assert treatment.name() == "miniswe-central"
    assert shadow.name() == "miniswe-central-shadow"


def test_context_compaction_uses_provider_headroom_reserve_not_only_char_threshold(tmp_path):
    agent = MiniSweCentralAgent(logs_dir=tmp_path, model_name="test")

    assert agent.context_capacity_chars == 400_000
    assert agent.context_trigger_chars == 120_000
    assert agent.context_target_chars == 80_000
    assert agent.provider_context_reserve_tokens == 131_072


def test_explicit_foreground_validation_may_receive_bounded_timeout_extension(tmp_path):
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        enable_adaptive_validation_timeout=True,
    )
    classification = classify_validation_command("timeout 90s python3 -m pytest -q")
    proposal = adapt_proposed_action(
        {"command": classification.command},
        source_revision="s1",
        workspace_revision="w1",
        model_call=1,
        batch_index=0,
        batch_size=1,
        validation=classification,
    )

    timeout, reason = agent._select_action_timeout(
        proposal,
        classification,
        remaining_agent_time_sec=500.0,
    )

    assert timeout == 90.0
    assert reason == "literal_validation_timeout"


def test_redirected_declared_validator_receives_bounded_timeout_extension(tmp_path):
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        enable_adaptive_validation_timeout=True,
    )
    command = "cd /app && timeout 900 python3 benchmark.py 2>&1"
    classification = classify_validation_command(command, ("python3 benchmark.py",))
    proposal = adapt_proposed_action(
        {"command": command},
        source_revision="s1",
        workspace_revision="w1",
        model_call=1,
        batch_index=0,
        batch_size=1,
        validation=classification,
    )

    timeout, reason = agent._select_action_timeout(
        proposal,
        classification,
        remaining_agent_time_sec=700.0,
    )

    assert timeout == 120.0
    assert reason == "literal_validation_timeout"


def test_timeout_extension_abstains_for_custom_or_dynamic_probes(tmp_path):
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        enable_adaptive_validation_timeout=True,
    )
    classification = classify_validation_command("timeout $WAIT python3 /tmp/test_one.py")
    proposal = adapt_proposed_action(
        {"command": classification.command},
        source_revision="s1",
        workspace_revision="w1",
        model_call=1,
        batch_index=0,
        batch_size=1,
        validation=classification,
    )

    timeout, reason = agent._select_action_timeout(
        proposal,
        classification,
        remaining_agent_time_sec=500.0,
    )

    assert timeout == 30.0
    assert reason == "default_command_timeout"


def test_dev_null_diagnostics_do_not_turn_read_only_search_into_mutation():
    command = "grep -rn needle . 2>/dev/null | head -20"
    proposal = adapt_proposed_action(
        {"command": command},
        source_revision="s1",
        workspace_revision="w1",
        model_call=1,
        batch_index=0,
        batch_size=1,
        validation=classify_validation_command(command),
    )

    assert proposal.operation is ActionOperation.SEARCH
    assert proposal.mutates_workspace is False
    assert proposal.mutation_certainty is MutationCertainty.PROVEN_READ_ONLY


@pytest.mark.parametrize("subcommand", ["gc", "reflog", "update-ref", "filter-branch"])
def test_irreversible_git_maintenance_is_typed_as_workspace_mutation(subcommand):
    arguments = {
        "gc": "--prune=now --aggressive",
        "reflog": "expire --expire=now --all",
        "update-ref": "-d refs/original/refs/heads/main",
        "filter-branch": "-f -- --all",
    }[subcommand]
    command = f"git {subcommand} {arguments}"
    proposal = adapt_proposed_action(
        {"command": command},
        source_revision="s1",
        workspace_revision="w1",
        model_call=1,
        batch_index=0,
        batch_size=1,
        validation=classify_validation_command(command),
    )

    assert proposal.mutates_workspace is True
    assert proposal.mutation_certainty is MutationCertainty.PROVEN_MUTATING
    assert proposal.operations[0].operation is ActionOperation.EDIT


def test_context_accounting_includes_reasoning_and_tool_calls():
    message = {
        "role": "assistant",
        "content": "",
        "reasoning_content": "reason",
        "tool_calls": [{"function": {"name": "bash", "arguments": '{"command":"pytest -q"}'}}],
    }

    assert _message_context_chars(message) > len("reason")


def test_paid_workflow_uses_external_central_agent_and_frozen_version():
    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "tb2_miniswe_engine.yml"
    ).read_text(encoding="utf-8")

    assert 'AGENT="eval.gt_central_agent:MiniSweCentralAgent"' in workflow
    assert '--agent-import-path "$AGENT"' in workflow
    assert '-a "$AGENT"' not in workflow
    assert '"mini-swe-agent==2.2.8"' in workflow
    assert "eval.miniswe_agent:MiniSweEngineAgent" not in workflow
    assert (
        "options: [off, audit, certified_context, certified_controllers, certified_full]"
        in workflow
    )
    assert "default: audit" in workflow
    assert "enable_lint=false" in workflow
    assert "preflight_mode=shadow" in workflow
    assert "enable_preflight=true" not in workflow


def test_paid_engine_workflow_receives_exact_harbor_budget_without_new_limit():
    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "tb2_miniswe_engine.yml"
    ).read_text(encoding="utf-8")

    # The same task.toml timeout still owns the experiment.  GT receives that
    # value only so it can return before Harbor asynchronously cancels run().
    assert "--ak enable_lint=true" in workflow
    assert "--ak enable_submit_readiness=true" in workflow
    assert "python scripts/resolve_harbor_budget.py" in workflow
    assert '--ak execution_budget_sec="$EXECUTION_BUDGET"' in workflow
    assert "--ak model_timeout_sec" not in workflow
    assert "--ak model_loop_timeout_sec" not in workflow
    assert "--agent-timeout-multiplier 1.0" in workflow
    assert "--ak enable_context_compaction=true" in workflow
    assert "--ak enable_completion_controller=true" in workflow
    assert "--ak enable_feature_guidance=false --ak enable_context_frontier=false" in workflow


def test_paid_central_matrix_uses_the_same_outcome_preserving_contract():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "tb2_miniswe_central.yml").read_text(
        encoding="utf-8"
    )
    descriptor = json.loads(
        (root / "eval" / "treatments" / "tb2_central_relational_v2.json").read_text(
            encoding="utf-8"
        )
    )
    runtime = descriptor["runtime_agent_kwargs"]

    assert 'AGENT="eval.gt_central_agent:MiniSweCentralAgent"' in workflow
    assert '--agent-import-path "$AGENT"' in workflow
    assert "python -m scripts.render_treatment_agent_args" in workflow
    assert "gt-treatment-runtime.json" in workflow
    assert "python -m scripts.build_benchmark_manifest" in workflow
    assert '--benchmark-manifest "$GT_BENCHMARK_MANIFEST_PATH"' in workflow
    assert "benchmark-manifest.json" in workflow
    assert "BenchmarkManifest.from_dict" in (
        root / "scripts" / "tb2_merge_results.py"
    ).read_text(encoding="utf-8")
    assert "audit_runtime_receipt" in (
        root / "scripts" / "tb2_merge_results.py"
    ).read_text(encoding="utf-8")
    assert descriptor["profile_id"] == "central_relational_v2"
    assert runtime["policy_mode"] == "certified_active"
    assert "inputs.arm" not in workflow
    assert "inputs.feature" not in workflow
    assert "--ak integration_mode=off" not in workflow
    assert "--ak integration_mode=audit" not in workflow
    assert runtime["preflight_mode"] == "assistive_safe"
    assert runtime["enable_context_compaction"] is True
    assert runtime["enable_adaptive_validation_timeout"] is True
    assert runtime["enable_completion_controller"] is True
    assert runtime["enable_progress_control"] is True
    assert descriptor["preemptive_retrieval"] is True
    assert descriptor["relational_context"] is True
    assert descriptor["semantic_evidence"] is True
    assert '--ak execution_budget_sec="$EXECUTION_BUDGET"' in workflow
    assert "from scripts.resolve_harbor_budget import resolve_budget" in workflow
    assert "--agent-timeout-multiplier 1.0" in workflow
    assert "--ak model_timeout_sec" not in workflow
    assert "--ak model_loop_timeout_sec" not in workflow
    assert "harbor_result=got[0] if got else None" in workflow
    assert "load_release_manifest().baseline_path" in workflow
    assert "eval/frozen_baselines/tb2_miniswe_20260731.json" not in workflow
    assert "assess_tb2_promotion" in workflow
    assert "build_feature_lifecycle_report" in workflow
    assert '"resolved_task_budgets": rows' in workflow
    assert '"timeout_policy_sha256": digest(timeout_policy)' in workflow
    assert '"execution_budget_sec": receipt["execution_budget_sec"]' in workflow


def test_merge_preserves_artifact_failures_when_runtime_checks_are_added():
    source = (
        Path(__file__).resolve().parents[1] / "scripts" / "tb2_merge_results.py"
    ).read_text(encoding="utf-8")

    assert "treatment_release_failures.extend(" in source
    assert "treatment_release_failures = [\n                failure" not in source
    assert '"task_execution_certificate_status"' in source
    assert '"provider_free_certification"' in source
    assert "run:provider_free_certification_invalid" in source


def test_paid_merge_retains_exact_provider_free_proof():
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github/workflows/tb2_miniswe_central.yml"
    ).read_text(encoding="utf-8")

    assert "needs: [resolve, provider_free, plan, run]" in workflow
    assert "central-provider-free-${{ github.run_id }}" in workflow
    assert "PROVIDER_FREE_COMMIT:" in workflow
    assert "PROVIDER_FREE_STATUS:" in workflow


@pytest.mark.asyncio
async def test_model_shell_receives_no_host_credentials_or_private_env(tmp_path):
    agent = MiniSweCentralAgent(logs_dir=tmp_path, model_name="test")
    environment = _Environment()
    context = AgentContext()

    class ScriptedModel:
        config = type("Config", (), {"model_name": "test"})()

        def format_message(self, **kwargs):
            return kwargs

        def get_template_vars(self):
            return {
                "observation_template": "{{ output.output }}",
                "format_error_template": "error",
            }

        def query(self, messages):
            return {
                "role": "assistant",
                "content": "submit",
                "extra": {
                    "actions": [
                        {
                            "command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",
                            "tool_call_id": "call-1",
                        }
                    ],
                    "response": {"usage": {}},
                    "cost": 0.0,
                },
            }

        def format_observation_messages(self, message, outputs, template_vars=None):
            return [{"role": "tool", "content": outputs[0]["output"]}]

    agent._model_factory = lambda: ScriptedModel()
    await agent.run("do the task", environment, context)

    model_actions = [item for item in environment.commands if "COMPLETE_TASK" in item[0]]
    assert len(model_actions) == 1
    assert model_actions[0][1] in (None, {})
    assert not any(
        name.startswith("GT_") for _, env in environment.commands for name in (env or {})
    )


@pytest.mark.asyncio
async def test_actual_loop_tracks_edit_lints_and_submits_without_private_context(tmp_path):
    class StatefulEnvironment(_Environment):
        def __init__(self):
            super().__init__()
            self.state = "empty"

        async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
            self.commands.append((command, env))
            if command.startswith("uname "):
                return ExecResult(stdout="Linux\t6.8\tversion\tx86_64\n", return_code=0)
            if "-printf" in command:
                if self.state == "empty":
                    raw = ""
                elif self.state == "bad":
                    raw = "f\t4\t2.0\t2.0\tapp.py\t\n"
                elif self.state == "good":
                    raw = "f\t3\t3.0\t3.0\tapp.py\t\n"
                else:
                    raw = "f\t5\t4.0\t4.0\tapp.py\t\n"
                return ExecResult(stdout=raw, return_code=0)
            if command.startswith("sha256sum"):
                return ExecResult(stdout=("a" * 64) + "  app.py\n", return_code=0)
            if "py_compile" in command:
                if self.state == "bad":
                    return ExecResult(stderr="SyntaxError: invalid syntax\n", return_code=1)
                return ExecResult(return_code=0)
            if command == "write bad":
                self.state = "bad"
                return ExecResult(return_code=0)
            if command == "write good":
                self.state = "good"
                return ExecResult(return_code=0)
            if command == "write better":
                self.state = "better"
                return ExecResult(return_code=0)
            if command == "pytest -q":
                return ExecResult(stdout="1 passed\n", return_code=0)
            if "COMPLETE_TASK" in command:
                return ExecResult(stdout="COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n", return_code=0)
            raise AssertionError(f"unexpected command: {command}")

    environment = StatefulEnvironment()
    model = _ScriptedModel(
        [
            "write bad",
            "write good",
            "write better",
            "pytest -q",
            "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",
        ]
    )
    agent = MiniSweCentralAgent(logs_dir=tmp_path, model_name="test")
    agent._model_factory = lambda: model
    context = AgentContext()

    await agent.run("Fix it, then run `pytest -q`.", environment, context)

    trajectory = (tmp_path / "miniswe_trajectory.json").read_text(encoding="utf-8")
    assert not any("Observed task fact:" in item for item in model.observed_history[0])
    assert any(
        "Observed task fact: Syntax check failed for app.py" in item
        for item in model.observed_history[1]
    )
    assert not any(
        "Observed task fact: Syntax check failed for app.py" in item
        for history in model.observed_history[2:]
        for item in history
    )
    assert any(
        "Unvalidated authored changes in app.py; declared check: pytest -q" in item
        for item in model.observed_history[3]
    )
    assert not any(
        "Unvalidated authored changes in app.py; declared check: pytest -q" in item
        for history in model.observed_history[4:]
        for item in history
    )
    # The durable trajectory stays clean; timing proof lives in receipt-v2.
    assert "Observed task fact:" not in trajectory
    assert "groundtruth" not in trajectory.lower()
    assert "gt_" not in trajectory.lower()
    assert context.metadata["exit_status"] == "Submitted"
    assert context.n_input_tokens == 50
    assert context.n_output_tokens == 10
    atif = (tmp_path / "trajectory.json").read_text(encoding="utf-8")
    assert '"schema_version": "ATIF-v1.7"' in atif
    assert '"function_name": "bash"' in atif
    receipt = json.loads((tmp_path / "central_receipt.json").read_text(encoding="utf-8"))
    metrics = receipt["metrics"]
    assert metrics["total_tokens"] == metrics["input_tokens"] + metrics["output_tokens"]
    assert metrics["api_calls"] == context.metadata["api_calls"]
    assert metrics["actions"] == context.metadata["actions"]
    assert metrics["assistant_steps"] == context.metadata["assistant_steps"]
    assert metrics["trajectory_messages"] >= metrics["assistant_steps"]
    assert metrics["uncached_input_tokens"] == 50
    assert metrics["prompt_cache_hit_rate"] == 0.0
    assert metrics["normalized_cost_usd"] > 0
    assert metrics["tokens_per_call"] == 12.0
    assert metrics["tokens_per_assistant_step"] == 12.0
    assert metrics["actions_per_assistant_step"] == 1.0
    assert metrics["elapsed_seconds"] > 0
    assert metrics["wall_time_sec"] == metrics["elapsed_seconds"]
    assert metrics["successful_actions"] == 5
    assert metrics["failed_actions"] == 0
    assert metrics["check_actions"] == 1
    assert metrics["workspace_change_actions"] == 3
    assert metrics["lint_passes"] == 2
    assert metrics["lint_failures"] == 1
    assert metrics["guidance_candidates"] >= metrics["guidance_events"]
    assert metrics["guidance_suppressed"] >= 1
    deliveries = receipt["guidance_deliveries"]
    assert len(deliveries) == 2
    assert deliveries[0]["feature_id"] == "syntax_result"
    assert deliveries[0]["evidence_action"] == 1
    assert deliveries[0]["delivered_before_call"] == 2
    assert deliveries[0]["decision_window"] == "first_next_model_call"
    assert deliveries[0]["not_predictive"] is True
    assert deliveries[1]["feature_id"] == "GT_EDIT_CHECK"
    assert deliveries[1]["evidence_action"] == 3
    assert deliveries[1]["delivered_before_call"] == 4
    assert deliveries[1]["decision_window"] == "first_next_model_call"
    assert deliveries[1]["not_predictive"] is True
    contexts = receipt["model_call_contexts"]
    assert len(contexts) == metrics["api_calls"]
    assert metrics["context_compiler_calls"] == metrics["api_calls"]
    assert metrics["context_unique_reasoning_chars_removed"] == 0
    assert metrics["context_compiler_effects_unaccounted"] == 0
    assert all(row["context_compiler_ran"] for row in contexts)
    assert all(row["context_facts_accounted"] == row["context_fact_candidates"] for row in contexts)
    assert all(
        row["context_compiler"]["accounted_fact_count"]
        == row["context_compiler"]["candidate_fact_count"]
        for row in contexts
    )
    assert contexts[1]["runtime_advisory_chars"] == deliveries[0]["chars"]
    assert contexts[1]["stock_context_chars"] > 0
    assert contexts[3]["runtime_advisory_chars"] == deliveries[1]["chars"]
    assert contexts[4]["runtime_advisory_chars"] == 0


@pytest.mark.asyncio
async def test_actual_agent_loop_routes_all_17_features_with_nonpredictive_effects(tmp_path):
    """Strict release proof: real agent lifecycle, not runtime-only fixtures."""

    submit = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"

    class AllFeatureEnvironment(_Environment):
        def __init__(self):
            super().__init__()
            self.state = "empty"

        async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
            self.commands.append((command, env))
            if command.startswith("uname "):
                return ExecResult(stdout="Linux\t6.8\tversion\tx86_64\n", return_code=0)
            if "-printf" in command:
                rows = {
                    "empty": "f\t3\t0.0\t0.0\tapp.py\t\n",
                    "s1": ("f\t4\t1.0\t1.0\tapp.py\t\nf\t3\t1.0\t1.0\tnew_module.py\t\n"),
                    "s2": ("f\t5\t2.0\t2.0\tapp.py\t\nf\t3\t1.0\t1.0\tnew_module.py\t\n"),
                    "s3": ("f\t6\t3.0\t3.0\tapp.py\t\nf\t3\t1.0\t1.0\tnew_module.py\t\n"),
                }
                return ExecResult(stdout=rows[self.state], return_code=0)
            if command.startswith("sha256sum"):
                paths = [path for path in ("app.py", "new_module.py") if path in command]
                return ExecResult(
                    stdout="".join(("a" * 64) + f"  {path}\n" for path in paths),
                    return_code=0,
                )
            if command.startswith("rg "):
                return ExecResult(
                    stdout=(
                        "app.py:10:def f(x)\n"
                        "tests/test_app.py:20:caller references f; existing registry pattern\n"
                    ),
                    return_code=0,
                )
            if command.startswith("sed -i"):
                self.state = "s1"
                return ExecResult(return_code=0)
            if command == "write update-1":
                self.state = "s2"
                return ExecResult(return_code=0)
            if command == "write update-2":
                self.state = "s3"
                return ExecResult(return_code=0)
            if "py_compile" in command:
                if self.state == "s1":
                    return ExecResult(stderr="SyntaxError: invalid syntax\n", return_code=1)
                return ExecResult(return_code=0)
            if command == "pytest -q":
                return ExecResult(stdout="1 failed: assertion error\n", return_code=1)
            if command == submit:
                return ExecResult(stdout=submit + "\n", return_code=0)
            raise AssertionError(f"unexpected command: {command}")

    model = _ScriptedModel(
        [
            "rg -n 'f|caller' .",
            "sed -i 's/def f(x)/def f(x, y)/' app.py",
            "write update-1",
            "write update-2",
            "pytest -q",
            "pytest -q",
            submit,
            submit,
        ]
    )

    class IndexedAllFeatureAgent(MiniSweCentralAgent):
        async def _start_repository_session(
            self,
            environment,
            instruction,
            *,
            snapshot,
            source_revision,
            task_deliverables=frozenset(),
        ):
            return (
                RepositoryEvidence(
                    available=True,
                    graph_revision="graph-r0",
                    anchors=(
                        {"path": "app.py", "line": 10, "symbol": "f"},
                        {"path": "tests/test_app.py", "line": 20, "symbol": "test_f"},
                    ),
                    definitions=(
                        {
                            "path": "app.py",
                            "line": 10,
                            "symbol": "f",
                            "semantics": "graph_definition",
                        },
                    ),
                    references=(
                        {
                            "path": "tests/test_app.py",
                            "line": 20,
                            "symbol": "f",
                            "semantics": "graph_call_reference",
                        },
                    ),
                    callers=(
                        {
                            "caller_path": "tests/test_app.py",
                            "caller_line": 20,
                            "caller_symbol": "test_f",
                            "target_path": "app.py",
                            "target_symbol": "f",
                            "semantics": "graph_recorded",
                        },
                    ),
                    status="available",
                ),
                None,
            )

    agent = IndexedAllFeatureAgent(logs_dir=tmp_path, model_name="test")
    agent._model_factory = lambda: model

    await agent.run(
        "Fix f, then run `pytest -q` before submitting.",
        AllFeatureEnvironment(),
        AgentContext(),
    )

    receipt = json.loads((tmp_path / "central_receipt.json").read_text(encoding="utf-8"))
    features = receipt["features"]
    assert set(features["consumer_paths"]) == set(features["feature_ids"])
    assert {row["feature_id"] for row in features["receipts"]} == set(features["feature_ids"])
    assert {row["feature_id"] for row in features["effects"]} == set(features["feature_ids"])
    assert {
        row["feature_id"] for row in features["effect_applications"] if row["state_fields_changed"]
    } == set(features["feature_ids"])
    assert all(row["evidence_before_effect"] for row in features["effects"])
    assert all(row["effect_before_next_action"] for row in features["effects"])
    assert all(row["non_late"] and not row["predictive"] for row in features["effects"])
    assert all(
        row["not_predictive"]
        and row["delivered_before_model_query"]
        and not row["one_step_late"]
        and row["delivered_before_call"] == row["first_eligible_call"]
        and row["request_payload_sha256"]
        == receipt["model_call_contexts"][row["delivered_before_call"] - 1][
            "request_payload_sha256"
        ]
        for row in receipt["guidance_deliveries"]
    )
    assert features["action_metrics"]["submit_holds"] == 0
    assert features["action_metrics"]["batch_interrupts"] == 0
    assert features["action_metrics"]["interrupted_actions"] == 0


@pytest.mark.asyncio
async def test_grounded_failure_warns_before_submit_without_holding_it(tmp_path):
    class CheckEnvironment(_Environment):
        async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
            self.commands.append((command, env))
            if command.startswith("uname "):
                return ExecResult(stdout="Linux\t6.8\tversion\tx86_64\n", return_code=0)
            if "-printf" in command:
                return ExecResult(stdout="", return_code=0)
            if command == "pytest -q":
                return ExecResult(stdout="failed\n", return_code=1)
            if "COMPLETE_TASK" in command:
                return ExecResult(stdout="COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n", return_code=0)
            raise AssertionError(command)

    submit = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
    environment = CheckEnvironment()
    # The spare third response keeps the RED witness finite under the old
    # submit-hold implementation.  The repaired loop must terminate after the
    # first submit and therefore issue only two model calls.
    model = _ScriptedModel(["pytest -q", submit, submit])
    agent = MiniSweCentralAgent(logs_dir=tmp_path, model_name="test")
    agent._model_factory = lambda: model

    await agent.run("Run `pytest -q` before finishing.", environment, AgentContext())

    executed_submits = [command for command, _ in environment.commands if command == submit]
    assert executed_submits == [submit]
    assert len(model.observed_history) == 2
    assert any("pytest -q" in item for item in model.observed_history[1])
    trajectory = (tmp_path / "miniswe_trajectory.json").read_text(encoding="utf-8")
    assert "Submit again to continue without another hold" not in trajectory
    receipt = json.loads((tmp_path / "central_receipt.json").read_text(encoding="utf-8"))
    assert receipt["metrics"]["submit_holds"] == 0


@pytest.mark.asyncio
async def test_shadow_submit_gate_holds_once_on_unverified_obligations(tmp_path):
    class CheckEnvironment(_Environment):
        async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
            self.commands.append((command, env))
            if command.startswith("uname "):
                return ExecResult(stdout="Linux\t6.8\tversion\tx86_64\n", return_code=0)
            if "-printf" in command:
                return ExecResult(stdout="", return_code=0)
            if "COMPLETE_TASK" in command:
                return ExecResult(stdout="COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n", return_code=0)
            raise AssertionError(command)

    submit = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
    environment = CheckEnvironment()
    model = _ScriptedModel([submit, submit])
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        integration_mode="active",
        preflight_mode="shadow",
        enable_shadow_submit_gate=True,
    )
    agent._model_factory = lambda: model

    task = (
        "Write a program extract.js that reads /app/a.out and writes the "
        "extracted integers to /app/out.json so that the values MUST match "
        "the reference solution."
    )
    await agent.run(task, environment, AgentContext())

    executed_submits = [command for command, _ in environment.commands if command == submit]
    assert executed_submits == [submit]
    assert len(model.observed_history) == 2
    assert any(
        "required task conditions remain unresolved" in item
        for item in model.observed_history[1]
    )
    trajectory = (tmp_path / "miniswe_trajectory.json").read_text(encoding="utf-8")
    assert "required task conditions remain unresolved" in trajectory
    assert "pre-execution check" not in trajectory.lower()
    receipt = json.loads((tmp_path / "central_receipt.json").read_text(encoding="utf-8"))
    assert receipt["metrics"]["submit_holds"] == 1
    assert receipt["metrics"]["submit_risks"] == 1
    feature_ids = {row["feature_id"] for row in receipt["features"]["receipts"]}
    assert "submit_refusal" in feature_ids
    assert "GT_SS_SUBMIT_RED" in feature_ids
    red = next(
        row
        for row in receipt["features"]["receipts"]
        if row["feature_id"] == "GT_SS_SUBMIT_RED"
    )
    assert red["payload"]["blockers"]
    assert "unverified" in red["payload"]["message"]


@pytest.mark.asyncio
async def test_assistive_safe_does_not_hold_on_unverified_prose_obligations(tmp_path):
    class CheckEnvironment(_Environment):
        async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
            self.commands.append((command, env))
            if command.startswith("uname "):
                return ExecResult(stdout="Linux\t6.8\tversion\tx86_64\n", return_code=0)
            if "-printf" in command:
                return ExecResult(stdout="", return_code=0)
            if "COMPLETE_TASK" in command:
                return ExecResult(
                    stdout="COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n", return_code=0
                )
            raise AssertionError(command)

    submit = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
    environment = CheckEnvironment()
    model = _ScriptedModel([submit, submit])
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        integration_mode="active",
        preflight_mode="assistive_safe",
    )
    agent._model_factory = lambda: model

    await agent.run(
        "Implement the algorithm correctly and match the stated behavior.",
        environment,
        AgentContext(),
    )

    executed_submits = [command for command, _ in environment.commands if command == submit]
    assert executed_submits == [submit]
    assert len(model.observed_history) == 1
    trajectory = (tmp_path / "miniswe_trajectory.json").read_text(encoding="utf-8")
    assert "required task conditions remain unresolved" not in trajectory
    receipt = json.loads((tmp_path / "central_receipt.json").read_text(encoding="utf-8"))
    assert receipt["metrics"]["submit_holds"] == 0


@pytest.mark.asyncio
async def test_assistive_safe_returns_fresh_standard_failure_before_submit(tmp_path):
    class CheckEnvironment(_Environment):
        async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
            self.commands.append((command, env))
            if command.startswith("uname "):
                return ExecResult(stdout="Linux\t6.8\tversion\tx86_64\n", return_code=0)
            if "-printf" in command:
                return ExecResult(stdout="", return_code=0)
            if command == "pytest -q":
                return ExecResult(stdout="1 failed\n", return_code=1)
            if "COMPLETE_TASK" in command:
                return ExecResult(stdout="COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n", return_code=0)
            raise AssertionError(command)

    submit = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
    environment = CheckEnvironment()
    model = _ScriptedModel(["pytest -q", submit, submit])
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        integration_mode="active",
        preflight_mode="assistive_safe",
    )
    agent._model_factory = lambda: model

    await agent.run("Run the project tests before finishing.", environment, AgentContext())

    executed_submits = [command for command, _ in environment.commands if command == submit]
    assert executed_submits == [submit]
    assert len(model.observed_history) == 3
    assert any("pytest -q" in item and "failing" in item for item in model.observed_history[2])
    receipt = json.loads((tmp_path / "central_receipt.json").read_text(encoding="utf-8"))
    assert receipt["metrics"]["submit_holds"] == 1


@pytest.mark.asyncio
async def test_assistive_safe_runs_one_discovered_project_check_at_submit(tmp_path):
    submit = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"

    class ProjectEnvironment(_ObservedMutationEnvironment):
        async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
            if command == "pytest -q":
                self.commands.append((command, env))
                return ExecResult(
                    stdout="tests/test_app.py::test_behavior FAILED\n",
                    return_code=1,
                )
            if command == submit:
                self.commands.append((command, env))
                return ExecResult(stdout=submit + "\n", return_code=0)
            return await super().exec(command, cwd, env, timeout_sec, user)

    class ProjectAgent(MiniSweCentralAgent):
        async def _start_repository_session(
            self,
            environment,
            instruction,
            *,
            snapshot,
            source_revision,
            task_deliverables=frozenset(),
        ):
            return (
                RepositoryEvidence(
                    available=True,
                    status="available",
                    project_checks=("pytest -q",),
                ),
                None,
            )

    environment = ProjectEnvironment(
        "touch app.py",
        "f\t6\t2.0\t2.0\tapp.py\t\n",
    )
    model = _ScriptedModel(["touch app.py", submit, submit])
    agent = ProjectAgent(
        logs_dir=tmp_path,
        model_name="test",
        integration_mode="active",
        preflight_mode="assistive_safe",
        enable_lint=False,
    )
    agent._model_factory = lambda: model

    await agent.run("Change the implementation and finish.", environment, AgentContext())

    commands = [command for command, _ in environment.commands]
    assert commands.count("pytest -q") == 1
    assert commands.count(submit) == 1
    assert any(
        "tests/test_app.py::test_behavior FAILED" in item for item in model.observed_history[2]
    )
    receipt = json.loads((tmp_path / "central_receipt.json").read_text(encoding="utf-8"))
    assert receipt["metrics"]["project_validation_probe_execs"] == 1
    assert receipt["project_validation"]["probes"][0]["status"] == "fail"
    assert receipt["metrics"]["controller_intervention_execs"] >= 1


@pytest.mark.asyncio
async def test_format_error_is_returned_to_model_instead_of_aborting(tmp_path):
    submit = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"

    class RecoveringModel(_ScriptedModel):
        def __init__(self):
            super().__init__([submit])
            self.queries = 0

        def query(self, messages):
            self.queries += 1
            if self.queries == 1:
                raise FormatError({"role": "user", "content": "Use the bash tool correctly."})
            return super().query(messages)

    class Environment(_Environment):
        async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
            self.commands.append((command, env))
            if command.startswith("uname "):
                return ExecResult(stdout="Linux\t6.8\tversion\tx86_64\n", return_code=0)
            if "-printf" in command:
                return ExecResult(stdout="", return_code=0)
            if command == submit:
                return ExecResult(stdout="COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n", return_code=0)
            raise AssertionError(command)

    model = RecoveringModel()
    agent = MiniSweCentralAgent(logs_dir=tmp_path, model_name="test")
    agent._model_factory = lambda: model

    await agent.run("do it", Environment(), AgentContext())

    assert model.queries == 2
    trajectory = (tmp_path / "miniswe_trajectory.json").read_text(encoding="utf-8")
    assert "Use the bash tool correctly" in trajectory


@pytest.mark.asyncio
async def test_model_timeout_writes_a_censored_partial_receipt(tmp_path):
    class ProviderTimeoutModel(_ScriptedModel):
        def __init__(self):
            super().__init__(["echo never-executed"])
            self.query_kwargs = None

        def query(self, messages, **kwargs):
            self.query_kwargs = dict(kwargs)
            raise TimeoutError("provider-enforced timeout")

    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        model_timeout_sec=0.001,
    )
    model = ProviderTimeoutModel()
    agent._model_factory = lambda: model
    context = AgentContext()

    await agent.run("do it", _Environment(), context)

    receipt = json.loads((tmp_path / "central_receipt.json").read_text(encoding="utf-8"))
    assert receipt["schema"] == "central-runtime-receipt-v3"
    assert receipt["metrics"]["censored"] is True
    assert receipt["metrics"]["censored_reason"] == "model_request_timeout"
    assert receipt["metrics"]["actions"] == 0
    assert model.query_kwargs == {"num_retries": 0, "timeout": pytest.approx(0.001)}
    assert context.metadata["exit_status"] == "ModelTimeout"


@pytest.mark.asyncio
async def test_litellm_timeout_is_recorded_as_provider_timeout_not_generic_error(tmp_path):
    import litellm

    class ProviderTimeoutModel(_ScriptedModel):
        def query(self, messages, **kwargs):
            raise litellm.exceptions.Timeout(
                message="provider timeout",
                model="test",
                llm_provider="test",
            )

    agent = MiniSweCentralAgent(logs_dir=tmp_path, model_name="test")
    agent._model_factory = lambda: ProviderTimeoutModel(["echo never-executed"])
    context = AgentContext()

    await agent.run("do it", _Environment(), context)

    receipt = json.loads((tmp_path / "central_receipt.json").read_text(encoding="utf-8"))
    assert receipt["metrics"]["censored"] is True
    assert receipt["metrics"]["censored_reason"] == "model_request_timeout"
    assert context.metadata["exit_status"] == "ModelTimeout"


@pytest.mark.asyncio
async def test_executor_never_abandons_provider_thread_with_host_wait_for(monkeypatch, tmp_path):
    class ProviderTimeoutModel(_ScriptedModel):
        def query(self, messages, **kwargs):
            assert kwargs["num_retries"] == 0
            assert kwargs["timeout"] == pytest.approx(0.001)
            raise TimeoutError("provider-enforced timeout")

    async def forbidden_wait_for(*args, **kwargs):
        raise AssertionError("executor must await the provider transport to completion")

    monkeypatch.setattr("eval.gt_central_agent.asyncio.wait_for", forbidden_wait_for)
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        model_timeout_sec=0.001,
    )
    agent._model_factory = lambda: ProviderTimeoutModel(["echo never-executed"])
    context = AgentContext()

    await agent.run("do it", _Environment(), context)

    receipt = json.loads((tmp_path / "central_receipt.json").read_text(encoding="utf-8"))
    assert context.metadata["exit_status"] == "ModelTimeout"
    assert receipt["metrics"]["provider_responses_received"] == 0


@pytest.mark.asyncio
async def test_provider_budget_failure_stops_before_model_query_and_is_receipted(tmp_path):
    class MustNotQuery(_ScriptedModel):
        def __init__(self):
            super().__init__(["echo never-executed"])
            self.queries = 0

        def query(self, messages):
            self.queries += 1
            raise AssertionError("an over-budget provider request must not be sent")

    model = MustNotQuery()
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        provider_context_limit_tokens=100,
        provider_context_hard_ratio=0.5,
    )
    agent._model_factory = lambda: model
    context = AgentContext()

    await agent.run("do it", _Environment(), context)

    assert model.queries == 0
    receipt = json.loads((tmp_path / "central_receipt.json").read_text(encoding="utf-8"))
    assert receipt["metrics"]["provider_request_budget_failures"] == 1
    assert receipt["metrics"]["censored"] is False
    assert receipt["metrics"]["censored_reason"] == ""
    assert receipt["metrics"]["solver_exhausted"] is True
    assert receipt["metrics"]["solver_exhausted_reason"] == "context_budget_exhausted"
    assert receipt["model_call_contexts"][0]["request_budget_within_limit"] is False
    assert receipt["metrics"]["provider_requests_prepared"] == 1
    assert receipt["metrics"]["model_query_invocations"] == 0
    assert receipt["metrics"]["provider_responses_received"] == 0
    assert receipt["metrics"]["provider_requests_not_sent"] == 1
    assert receipt["metrics"]["api_calls"] == 0
    assert receipt["model_call_contexts"][0]["dispatch_status"] == "prepared_not_sent"
    assert context.metadata["exit_status"] == "ContextBudgetExhausted"


@pytest.mark.asyncio
async def test_stall_aggregate_reaches_first_next_model_call_once_without_advice(tmp_path):
    model = _ScriptedModel(
        [
            *("printf same" for _ in range(12)),
            "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",
        ]
    )
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        enable_repository_intelligence=False,
        enable_progress_control=True,
    )
    agent._model_factory = lambda: model

    await agent.run("Complete the task.", _Environment(), AgentContext())

    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    deliveries = receipt["progress"]["fact_deliveries"]
    assert deliveries == []
    assert receipt["metrics"]["progress_frame_deliveries"] == 0
    visible = "\n".join(model.observed_history[12])
    assert "Execution state STALLED" not in visible
    assert any(
        row["surface"] == "progress_frame" and row["disposition"] == "value_rejected"
        for call in receipt["contribution_compiler"]["calls"]
        for row in call["accounting"]
    )


@pytest.mark.asyncio
async def test_distinct_successful_experiments_do_not_emit_false_stall(tmp_path):
    model = _ScriptedModel(
        [
            "python3 -c 'print(1)'",
            "python3 -c 'print(2)'",
            "python3 -c 'print(3)'",
            "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",
        ]
    )
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        enable_repository_intelligence=False,
        enable_progress_control=True,
    )
    agent._model_factory = lambda: model

    await agent.run("Complete the task.", _Environment(), AgentContext())

    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    assert receipt["metrics"]["progress_frame_deliveries"] == 0
    assert not any(
        row["current"] in {"STALLED", "CONTRADICTED"} for row in receipt["progress"]["transitions"]
    )
    observations = receipt["progress"]["observations"][:3]
    assert len({row["command_sha256"] for row in observations}) == 3
    assert len({row["attempt_id"] for row in observations}) == 3


@pytest.mark.asyncio
async def test_failed_reader_does_not_consume_anchor_or_create_fallback_stall(tmp_path):
    class ReaderEnvironment(_Environment):
        async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
            self.commands.append((command, env))
            if command.startswith("uname "):
                return ExecResult(stdout="Linux\t6.8\tversion\tx86_64\n", return_code=0)
            if "-printf" in command:
                return ExecResult(stdout="", return_code=0)
            if command.startswith("xxd "):
                return ExecResult(stdout="xxd: command not found", return_code=127)
            if command.startswith("od "):
                return ExecResult(stdout=f"bytes for {command.split()[-1]}", return_code=0)
            return ExecResult(stdout="", return_code=0)

    paths = ("a.cob", "b.cob", "c.cob", "d.cob")
    model = _ScriptedModel(
        [
            *(f"xxd {path}" for path in paths),
            *(f"od {path}" for path in paths),
            "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",
        ]
    )
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        enable_repository_intelligence=False,
        enable_progress_control=True,
    )
    agent._model_factory = lambda: model

    await agent.run("Inspect the COBOL inputs and finish.", ReaderEnvironment(), AgentContext())

    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    assert receipt["metrics"]["progress_frame_deliveries"] == 0
    assert receipt["metrics"]["failed_read_anchors_not_consumed"] == 4
    assert receipt["metrics"]["semantic_progress_kinds"]["localization_gain"] == 4
    assert len(receipt["progress"]["observations"]) == 9
    assert all(
        len(row["output_sha256"]) == 64
        and "declared_check_id" in row
        and "diagnostic_fingerprint" in row
        for row in receipt["progress"]["observations"]
    )
    assert all(
        "Execution state STALLED" not in "\n".join(history) for history in model.observed_history
    )


@pytest.mark.asyncio
async def test_over_budget_next_request_does_not_confirm_pending_guidance(tmp_path):
    class LargeFailureEnvironment(_Environment):
        async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
            self.commands.append((command, env))
            if command.startswith("uname "):
                return ExecResult(stdout="Linux\t6.8\tversion\tx86_64\n", return_code=0)
            if "-printf" in command:
                return ExecResult(stdout="", return_code=0)
            if command == "pytest -q":
                return ExecResult(
                    stdout="FAILED tests/test_app.py::test_app\n" + ("x" * 50_000),
                    return_code=1,
                )
            raise AssertionError(command)

    model = _ScriptedModel(["pytest -q", "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"])
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        enable_context_compaction=True,
        provider_context_limit_tokens=30_000,
        provider_context_hard_ratio=0.5,
    )
    agent._model_factory = lambda: model

    await agent.run(
        "Run `pytest -q` before finishing.",
        LargeFailureEnvironment(),
        AgentContext(),
    )

    assert len(model.observed_history) == 1
    receipt = json.loads((tmp_path / "central_receipt.json").read_text(encoding="utf-8"))
    assert receipt["metrics"]["provider_request_budget_failures"] == 1
    assert receipt["metrics"]["payload_deliveries"] == 0
    assert receipt["guidance_deliveries"] == []
    assert receipt["model_call_contexts"][-1]["runtime_message_index"] is not None
    assert receipt["model_call_contexts"][-1]["request_budget_within_limit"] is False


@pytest.mark.asyncio
async def test_executable_completion_certificate_auto_submits_before_next_model_call(tmp_path):
    task = """Please solve this issue: Write me data.comp that's compressed such that
running cat data.comp | /app/decomp gives exactly data.txt.
You can generate data.comp any way you want, but data.comp must be at most 2500 bytes.

## Recommended Workflow
1. Analyze the codebase
2. Submit with echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT
"""

    class ArtifactEnvironment(_Environment):
        def __init__(self):
            super().__init__()
            self.created = False

        async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
            self.commands.append((command, env))
            if command.startswith("uname "):
                return ExecResult(stdout="Linux\t6.8\tversion\tx86_64\n", return_code=0)
            if "-printf" in command:
                manifest = "f\t100\t1.0\t1.0\tdata.txt\t\nf\t100\t1.0\t1.0\tdecomp\t\n"
                if self.created:
                    manifest += "f\t2372\t2.0\t2.0\tdata.comp\t\n"
                return ExecResult(stdout=manifest, return_code=0)
            if command.startswith("sha256sum"):
                return ExecResult(stdout=("a" * 64) + "  data.comp\n", return_code=0)
            if command == "write candidate":
                self.created = True
                return ExecResult(return_code=0)
            if command.startswith("tmp=$(mktemp)"):
                return ExecResult(return_code=0)
            if command.startswith("test -f /app/data.comp"):
                return ExecResult(return_code=0)
            if command == "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT":
                return ExecResult(stdout="COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n", return_code=0)
            raise AssertionError(f"unexpected command: {command}")

    model = _ScriptedModel(["write candidate"])
    environment = ArtifactEnvironment()
    agent = MiniSweCentralAgent(logs_dir=tmp_path, model_name="test")
    agent._model_factory = lambda: model
    context = AgentContext()

    await agent.run(task, environment, context)

    assert len(model.observed_history) == 1
    executed = [command for command, _ in environment.commands]
    assert executed.count("echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT") == 1
    receipt = json.loads((tmp_path / "central_receipt.json").read_text(encoding="utf-8"))
    certificate = receipt["completion"]["latest_certificate"]
    assert certificate["status"] == "complete"
    assert certificate["auto_submit_eligible"] is True
    assert receipt["completion"]["auto_submit_count"] == 1
    assert receipt["metrics"]["auto_submits"] == 1
    assert receipt["metrics"]["actions"] == 1
    # Extensionless files are captured once so content-signature sources can
    # be classified and mirrored rather than silently omitted from graph
    # substrate discovery.
    assert receipt["metrics"]["actual_environment_execs"] == 10
    assert receipt["metrics"]["effective_actions"] == 1
    assert receipt["metrics"]["legacy_effective_task_environment_execs"] == 9
    assert receipt["metrics"]["sensor_environment_execs"] == 5
    assert receipt["metrics"]["controller_environment_execs"] == 9
    assert receipt["metrics"]["effective_actions_schema"] == "model-selected-tool-actions-v3"
    assert receipt["actor_action_accounting"]["counts"] == {
        "MODEL_DECISION": 1,
        "TOOL_ACTION": 1,
        "CONTROLLER_ACTION": 3,
        "SUBSTRATE_PROBE": 5,
        "HOST_OTHER": 1,
    }
    assert receipt["actor_action_accounting"]["conservation_valid"] is True
    assert receipt["runtime_lifecycle"]["model_agnostic"] is True
    assert receipt["runtime_lifecycle"]["lifecycle_conservation_valid"] is True
    assert receipt["runtime_lifecycle"]["action_conservation_valid"] is True
    assert receipt["runtime_lifecycle"]["complete"] is True
    assert receipt["metrics"]["host_exec_category_counts"]["model_action"] == 1
    assert receipt["metrics"]["host_exec_category_counts"]["completion_probe"] == 2
    assert receipt["metrics"]["host_exec_category_counts"]["auto_submit"] == 1
    gt_certificate = next(
        row for row in receipt["features"]["receipts"] if row["feature_id"] == "GT_CERT_DELIVERY"
    )
    assert gt_certificate["payload"]["check_count"] == 2
    assert gt_certificate["payload"]["passing_checks"] == 2
    assert gt_certificate["payload"]["readiness"] == "validated"
    assert context.metadata["exit_status"] == "Submitted"


@pytest.mark.asyncio
async def test_execution_budget_reserve_exits_before_outer_timeout(tmp_path):
    class SlowModel(_ScriptedModel):
        def query(self, messages):
            time.sleep(0.05)
            return super().query(messages)

    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        execution_budget_sec=0.03,
        deadline_reserve_sec=0.01,
    )
    agent._model_factory = lambda: SlowModel(["echo too-late"])
    context = AgentContext()

    await agent.run("do it", _Environment(), context)

    receipt = json.loads((tmp_path / "central_receipt.json").read_text(encoding="utf-8"))
    assert receipt["metrics"]["censored"] is False
    assert receipt["metrics"]["deadline_reserve_exits"] == 1
    assert receipt["deadline"]["execution_budget_sec"] == 0.03
    assert context.metadata["exit_status"] == "DeadlineReserveReached"


@pytest.mark.asyncio
async def test_provider_request_is_not_started_when_only_teardown_reserve_remains(tmp_path):
    class MustNotQuery(_ScriptedModel):
        def query(self, messages, **kwargs):
            raise AssertionError("provider request must not start inside deadline reserve")

    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        execution_budget_sec=0.5,
        deadline_reserve_sec=0.1,
        model_timeout_sec=0.1,
        enable_replay_capture=True,
    )
    model = MustNotQuery(["echo never-executed"])
    agent._model_factory = lambda: model
    context = AgentContext()

    await agent.run("do it", _Environment(), context)

    receipt = json.loads((tmp_path / "central_receipt.json").read_text(encoding="utf-8"))
    assert receipt["metrics"]["censored"] is False
    assert receipt["metrics"]["provider_requests_not_sent"] == 1
    assert receipt["metrics"]["provider_responses_received"] == 0
    assert receipt["replay_bundle"]["trajectory_replay_ready"] is True
    assert context.metadata["exit_status"] == "DeadlineReserveReached"


@pytest.mark.asyncio
async def test_syntax_failure_does_not_interrupt_multi_action_batch(tmp_path):
    submit = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"

    class MultiActionModel:
        config = type("Config", (), {"model_name": "test"})()
        queries = 0
        observed_history: list[list[str]] = []

        def format_message(self, **kwargs):
            return kwargs

        def get_template_vars(self):
            return {
                "observation_template": "{{ output.output }}",
                "format_error_template": "error",
            }

        def query(self, messages):
            type(self).queries += 1
            type(self).observed_history.append(
                [str(item.get("content") or "") for item in messages]
            )
            if type(self).queries == 1:
                return {
                    "role": "assistant",
                    "content": "act",
                    "extra": {
                        "actions": [
                            {"command": "write broken", "tool_call_id": "call-1"},
                            {"command": "echo MUST_NOT_EXECUTE", "tool_call_id": "call-2"},
                            {"command": "echo ALSO_MUST_NOT_EXECUTE", "tool_call_id": "call-3"},
                        ],
                        "response": {
                            "usage": {
                                "prompt_tokens": 10,
                                "completion_tokens": 2,
                                "total_tokens": 12,
                            }
                        },
                        "cost": 0.0,
                    },
                }
            return {
                "role": "assistant",
                "content": "submit",
                "extra": {
                    "actions": [{"command": submit, "tool_call_id": "call-4"}],
                    "response": {
                        "usage": {
                            "prompt_tokens": 10,
                            "completion_tokens": 2,
                            "total_tokens": 12,
                        }
                    },
                    "cost": 0.0,
                },
            }

        def format_observation_messages(self, message, outputs, template_vars=None):
            return [{"role": "tool", "content": outputs[i]["output"]} for i in range(len(outputs))]

    class InterruptEnvironment(_Environment):
        def __init__(self):
            super().__init__()
            self.state = "empty"

        async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
            self.commands.append((command, env))
            if command.startswith("uname "):
                return ExecResult(stdout="Linux\t6.8\tversion\tx86_64\n", return_code=0)
            if "-printf" in command:
                if self.state == "empty":
                    raw = ""
                elif self.state == "bad":
                    raw = "f\t4\t2.0\t2.0\tapp.py\t\n"
                else:
                    raw = "f\t3\t3.0\t3.0\tapp.py\t\n"
                return ExecResult(stdout=raw, return_code=0)
            if command.startswith("sha256sum"):
                return ExecResult(stdout=("a" * 64) + "  app.py\n", return_code=0)
            if "py_compile" in command:
                if self.state == "bad":
                    return ExecResult(stderr="SyntaxError: invalid syntax\n", return_code=1)
                return ExecResult(return_code=0)
            if command == "write broken":
                self.state = "bad"
                return ExecResult(return_code=0)
            if command in {"echo MUST_NOT_EXECUTE", "echo ALSO_MUST_NOT_EXECUTE"}:
                return ExecResult(stdout=command + "\n", return_code=0)
            if command == submit:
                return ExecResult(stdout=submit + "\n", return_code=0)
            raise AssertionError(f"unexpected command: {command}")

    environment = InterruptEnvironment()
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        enable_submit_readiness=False,
    )
    agent._model_factory = lambda: MultiActionModel()
    context = AgentContext()

    await agent.run("Fix the syntax error.", environment, context)

    executed = [
        command
        for command, _ in environment.commands
        if not command.startswith("uname")
        and "-printf" not in command
        and not command.startswith("sha256sum")
        and not command.startswith("python3 -c")
        and "py_compile" not in command
    ]
    assert executed == [
        "write broken",
        "echo MUST_NOT_EXECUTE",
        "echo ALSO_MUST_NOT_EXECUTE",
        submit,
    ]
    assert not any(
        "Syntax check failed for app.py" in item for item in MultiActionModel.observed_history[0]
    )
    assert any(
        "Syntax check failed for app.py" in item for item in MultiActionModel.observed_history[1]
    )
    receipt = json.loads((tmp_path / "central_receipt.json").read_text(encoding="utf-8"))
    assert receipt["metrics"]["batch_interrupts"] == 0
    assert receipt["metrics"]["interrupted_actions"] == 0
    assert receipt["features"]["batch_interrupts"] == []
    syntax_effect = next(
        effect
        for effect in receipt["features"]["effects"]
        if effect["feature_id"] == "syntax_result"
    )
    assert syntax_effect["predecided_actions_cancelled"] == 0
    assert syntax_effect["predecided_actions_executed_after_evidence"] == 2
    assert receipt["guidance_deliveries"][0]["delivered_before_call"] == 2
    assert receipt["guidance_deliveries"][0]["first_eligible_call"] == 2
    assert receipt["guidance_deliveries"][0]["delivered_before_model_query"] is True
    assert context.metadata["exit_status"] == "Submitted"
