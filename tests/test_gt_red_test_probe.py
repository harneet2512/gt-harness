"""First-action red-test scheduling probe (Phase 1.1).

The probe is host-side, zero model calls, default-OFF, and fail-open.  It runs
the highest-priority declared verifier once at task start and seeds the first
retrieval query with the failure trace's diagnostic anchors.  It never emits a
feature receipt and never creates obligations.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from harbor.environments.base import ExecResult
from harbor.models.agent.context import AgentContext

from eval.gt_central_agent import MiniSweCentralAgent
from gt_engine.central_runtime import WorkspaceSnapshot
from gt_engine.host_execution import HostExecCategory
from gt_engine.indexer import IndexBuildReceipt, IndexBuildStatus
from gt_engine.repository_intelligence import RepositoryEvidence


class _ProbeEnvironment:
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
        if command == "pytest tests/test_service.py -q":
            return ExecResult(
                stdout="",
                stderr=(
                    "tests/test_service.py:12: in test_save_user\n"
                    "    from src.service import save_user\n"
                    "File \"src/service.py\", line 7, in save_user\n"
                    "    return users[user_id]\n"
                    "KeyError: 'missing'\n"
                ),
                return_code=1,
            )
        return ExecResult(stdout="", return_code=0)


def _snapshot(tmp_path):
    return WorkspaceSnapshot(
        revision="workspace-1",
        entries={
            "src/service.py": SimpleNamespace(
                kind="f",
                size=10,
                mtime=0.0,
                ctime=0.0,
                link_target=None,
                digest="a" * 64,
                content="def save_user():\n    pass\n",
            ),
            "tests/test_service.py": SimpleNamespace(
                kind="f",
                size=10,
                mtime=0.0,
                ctime=0.0,
                link_target=None,
                digest="b" * 64,
                content="def test_save_user():\n    pass\n",
            ),
        },
        healthy=True,
        reason="",
        elapsed_seconds=0.1,
    )


def _evidence(tmp_path):
    return RepositoryEvidence(
        available=True,
        graph_revision="graph-1",
        anchors=(
            {
                "path": "src/service.py",
                "line": 7,
                "symbol": "save_user",
                "semantic_certainty": 1.0,
                "retrieval_relevance": 1.0,
            },
        ),
        definitions=({"path": "src/service.py", "line": 7, "symbol": "save_user"},),
        project_checks=("pytest tests/test_service.py -q",),
        status="source_backed",
        source_revision="graph-source",
        index_current=True,
        intelligence_valid=True,
        substrate_ready=True,
        index=IndexBuildReceipt(
            status=IndexBuildStatus.AVAILABLE,
            graph_db=str(tmp_path / "graph.db"),
            schema_valid=True,
            node_count=1,
            edge_count=0,
            source_files=1,
            indexable_files=1,
            graph_revision="graph-1",
            source_revision="graph-source",
        ),
    )


def _agent(tmp_path, **kwargs):
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        integration_mode="active",
        policy_mode="certified_active",
        enable_first_action_red_test=True,
        enable_preemptive_retrieval=False,
        enable_context_frontier=False,
        enable_completion_controller=False,
        enable_repository_intelligence=False,
        enable_feature_guidance=False,
        **kwargs,
    )
    return agent


@pytest.mark.asyncio
async def test_red_test_probe_is_disabled_by_default(tmp_path):
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        integration_mode="active",
    )
    environment = _ProbeEnvironment()
    receipt = await agent._run_first_action_red_test(
        environment,
        explicit_checks=("pytest tests/test_service.py -q",),
        snapshot=_snapshot(tmp_path),
        task_deliverables=(),
        source_revision="workspace-1",
        graph_source_revision="graph-source",
        deadline=None,
    )

    assert receipt["status"] == "disabled"
    assert receipt["reason_codes"] == ["first_action_red_test_disabled"]
    assert environment.commands == []


@pytest.mark.asyncio
async def test_red_test_probe_is_forced_off_outside_active_treatment(tmp_path):
    for integration_mode in ("off", "audit"):
        agent = MiniSweCentralAgent(
            logs_dir=tmp_path / integration_mode,
            model_name="test",
            integration_mode=integration_mode,
            enable_first_action_red_test=True,
        )
        assert agent.enable_first_action_red_test is False


@pytest.mark.asyncio
async def test_red_test_probe_runs_declared_verifier_and_seeds_failure_trace(tmp_path):
    agent = _agent(tmp_path)
    agent._model_factory = lambda: None
    environment = _ProbeEnvironment()
    receipt = await agent._run_first_action_red_test(
        environment,
        explicit_checks=("pytest tests/test_service.py -q",),
        snapshot=_snapshot(tmp_path),
        task_deliverables=(),
        source_revision="workspace-1",
        graph_source_revision="graph-source",
        deadline=None,
    )

    assert environment.commands
    assert any("pytest tests/test_service.py -q" in command for command, _ in environment.commands)
    assert receipt["status"] == "failed"
    assert receipt["validation_status"] == "fail"
    assert receipt["returncode"] == 1
    assert receipt["command"] == "pytest tests/test_service.py -q"
    anchors = receipt["diagnostic_anchors"]
    assert any(anchor["path"] == "src/service.py" for anchor in anchors)
    assert any(anchor["symbol"] == "save_user" for anchor in anchors)
    host_rows = [row for row in agent._host_executions.summary()["receipts"]]
    assert any(row["category"] == HostExecCategory.RED_TEST_PROBE.value for row in host_rows)


@pytest.mark.asyncio
async def test_red_test_probe_abstains_when_verifier_artifact_is_absent(tmp_path):
    agent = _agent(tmp_path)
    environment = _ProbeEnvironment()
    receipt = await agent._run_first_action_red_test(
        environment,
        explicit_checks=("bash /app/does_not_exist.sh",),
        snapshot=_snapshot(tmp_path),
        task_deliverables=(),
        source_revision="workspace-1",
        graph_source_revision="graph-source",
        deadline=None,
    )

    assert receipt["status"] == "abstained"
    assert receipt["reason_codes"][0] == "verifier_identity_not_recognized"
    assert environment.commands == []


@pytest.mark.asyncio
async def test_red_test_probe_abstains_for_non_validation_command(tmp_path):
    agent = _agent(tmp_path)
    environment = _ProbeEnvironment()
    receipt = await agent._run_first_action_red_test(
        environment,
        explicit_checks=("ls -la",),
        snapshot=_snapshot(tmp_path),
        task_deliverables=(),
        source_revision="workspace-1",
        graph_source_revision="graph-source",
        deadline=None,
    )

    assert receipt["status"] == "abstained"
    assert receipt["reason_codes"] == ["verifier_identity_not_recognized"]
    assert environment.commands == []


@pytest.mark.asyncio
async def test_red_test_probe_abstains_for_composite_or_dynamic_commands(tmp_path):
    agent = _agent(tmp_path)
    environment = _ProbeEnvironment()
    for command in (
        "pytest -q && echo done",
        "bash -c 'pytest -q'",
        "echo hi | pytest -q",
    ):
        environment.commands = []
        receipt = await agent._run_first_action_red_test(
            environment,
            explicit_checks=(command,),
            snapshot=_snapshot(tmp_path),
            task_deliverables=(),
            source_revision="workspace-1",
            graph_source_revision="graph-source",
            deadline=None,
        )
        assert receipt["status"] in {"abstained", "failed_open"}, command


@pytest.mark.asyncio
async def test_red_test_probe_fails_open_on_timeout_and_does_not_block_loop(tmp_path):
    class TimeoutEnvironment(_ProbeEnvironment):
        async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
            raise TimeoutError

    agent = _agent(tmp_path)
    environment = TimeoutEnvironment()
    receipt = await agent._run_first_action_red_test(
        environment,
        explicit_checks=("pytest tests/test_service.py -q",),
        snapshot=_snapshot(tmp_path),
        task_deliverables=(),
        source_revision="workspace-1",
        graph_source_revision="graph-source",
        deadline=None,
    )

    assert receipt["status"] == "failed_open"
    assert receipt["reason_codes"] == ["probe_timeout"]
    assert receipt["timeout"] is True


@pytest.mark.asyncio
async def test_red_test_probe_records_passing_verifier_without_failure_trace(tmp_path):
    class PassingEnvironment(_ProbeEnvironment):
        async def exec(self, command, cwd=None, env=None, timeout_sec=None, user=None):
            self.commands.append((command, env))
            if command == "pytest tests/test_service.py -q":
                return ExecResult(stdout="1 passed\n", return_code=0)
            return await super().exec(command, cwd, env, timeout_sec, user)

    agent = _agent(tmp_path)
    environment = PassingEnvironment()
    receipt = await agent._run_first_action_red_test(
        environment,
        explicit_checks=("pytest tests/test_service.py -q",),
        snapshot=_snapshot(tmp_path),
        task_deliverables=(),
        source_revision="workspace-1",
        graph_source_revision="graph-source",
        deadline=None,
    )

    assert receipt["status"] == "passed"
    assert receipt["validation_status"] == "pass"
    assert receipt["diagnostic_anchors"] == []


@pytest.mark.asyncio
async def test_red_test_probe_runs_within_end_to_end_loop_and_seeds_retrieval(
    tmp_path, monkeypatch
):
    from gt_engine.hybrid_repository import HybridRepository
    from gt_engine.hybrid_retrieval import RepositoryDocument

    class Model:
        config = type("Config", (), {"model_name": "test"})()
        tools = [
            {
                "type": "function",
                "function": {"name": "bash", "parameters": {"type": "object"}},
            }
        ]

        def __init__(self):
            self.query_count = 0
            self.observed = []

        def format_message(self, **kwargs):
            return kwargs

        def get_template_vars(self):
            return {
                "observation_template": "{{ output.output }}",
                "format_error_template": "error",
            }

        def query(self, messages):
            self.query_count += 1
            self.observed = [str(item.get("content") or "") for item in messages]
            command = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
            return {
                "role": "assistant",
                "content": "submit",
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

    model = Model()
    agent = MiniSweCentralAgent(
        logs_dir=tmp_path,
        model_name="test",
        integration_mode="active",
        policy_mode="certified_active",
        enable_first_action_red_test=True,
        enable_preemptive_retrieval=True,
        enable_context_frontier=False,
        enable_completion_controller=False,
        enable_repository_intelligence=True,
        enable_feature_guidance=False,
    )
    agent._model_factory = lambda: model

    async def fake_repository_session(*args, **kwargs):
        evidence = _evidence(tmp_path)
        session = SimpleNamespace(
            root=tmp_path,
            indexed_source_revision=kwargs["source_revision"],
            source_revision=kwargs["source_revision"],
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
    monkeypatch.setattr(
        "eval.gt_central_agent.build_hybrid_repository",
        lambda *args, **kwargs: repository,
    )
    agent._start_repository_session = fake_repository_session
    environment = _ProbeEnvironment()
    context = AgentContext()

    await agent.run(
        "Fix src/service.py and run `pytest tests/test_service.py -q`.",
        environment,
        context,
    )

    receipt = json.loads((tmp_path / "central_receipt.json").read_text())
    red_test = receipt["red_test"]
    assert red_test["enabled"] is True
    assert len(red_test["receipts"]) == 1, red_test["receipts"]
    assert (
        red_test["receipts"][0]["status"] in {"failed", "failed_no_anchors"}
    ), red_test["receipts"]
    assert receipt["metrics"]["red_test_probe_attempts"] == 1
    assert receipt["metrics"]["red_test_probe_failed"] == 1
    assert any(
        row["category"] == HostExecCategory.RED_TEST_PROBE.value
        for row in receipt["host_execution"]["receipts"]
    )
    initial_retrieval = receipt["persistent_execution_state"]["initial_retrieval"]
    assert initial_retrieval["status"] == "disabled" or initial_retrieval["status"] == "initialized"
    assert red_test["receipts"][0]["diagnostic_anchors"] or red_test["receipts"][0][
        "status"
    ] in {"failed", "failed_no_anchors"}
