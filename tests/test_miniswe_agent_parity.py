from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from harbor.agents.installed.base import NonZeroAgentExitCodeError

from eval.miniswe_agent import (
    _DEFAULT_MINISWE_AGENT_VERSION,
    _REMOTE_LSP_BIN,
    MiniSweAgent,
    MiniSweGtAgent,
    _miniswe_agent_version,
)


@pytest.fixture(autouse=True)
def _host_attestation_key(monkeypatch):
    monkeypatch.setenv("GT_RESOURCE_ATTESTATION_KEY", "f" * 64)


def test_gt_off_and_gt_on_share_the_exact_installer_implementation():
    assert MiniSweGtAgent.install is MiniSweAgent.install


def test_miniswe_agent_version_defaults_to_current_release(monkeypatch):
    monkeypatch.delenv("MINISWE_AGENT_VERSION", raising=False)

    assert _DEFAULT_MINISWE_AGENT_VERSION == "2.4.6"
    assert _miniswe_agent_version() == "2.4.6"


def test_miniswe_agent_version_rejects_historical_matched_release(monkeypatch):
    monkeypatch.setenv("MINISWE_AGENT_VERSION", "2.2.8")
    with pytest.raises(ValueError, match="2.4.6"):
        _miniswe_agent_version()


def test_miniswe_agent_version_rejects_every_other_value(monkeypatch):
    monkeypatch.setenv("MINISWE_AGENT_VERSION", "2.2.9")

    try:
        _miniswe_agent_version()
    except ValueError as exc:
        assert "MINISWE_AGENT_VERSION" in str(exc)
        assert "2.4.6" in str(exc)
    else:
        raise AssertionError("unsupported Mini-SWE version was accepted")


def test_workflow_max_iterations_reaches_the_installed_runner(tmp_path):
    agent = MiniSweAgent(
        logs_dir=tmp_path / "logs",
        max_iterations=300,
        task_id="task-a",
        product_source_sha="a" * 40,
        time_budget_seconds=3600,
    )

    command = agent._run_command("task", "deepseek-v4-flash")

    assert "--step-limit 300" in command
    assert "--task-id task-a" in command
    assert f"--product-source-sha {'a' * 40}" in command
    assert "--time-budget-seconds 3600" in command
    assert "--product-receipt /logs/agent/gt-run.json" in command
    assert "--adapter-receipt /logs/agent/benchmark-adapter.json" in command
    assert "--patch-output /logs/artifacts/model.patch" in command
    # The installed runner must be what executes, with nothing wrapping it. The
    # only permitted prefix is the staged language-server PATH, which changes
    # lookup for the promotion servers and not the interpreter.
    prefix, _, tail = command.partition("exec ")
    assert tail.startswith('"$HOME/.local/share/uv/tools/nano-harness/bin/python"')
    assert prefix == f'PATH="{_REMOTE_LSP_BIN}:$PATH" '
    assert "scripts.provider_probe" not in command


def test_installed_agent_resolves_explicit_verified_bundle_artifacts(monkeypatch, tmp_path):
    gt_wheel = tmp_path / "groundtruth_mcp-1.0.0-py3-none-any.whl"
    harness_wheel = tmp_path / "nano_harness-0.0.1-py3-none-any.whl"
    gt_wheel.write_bytes(b"groundtruth")
    harness_wheel.write_bytes(b"harness")
    monkeypatch.setenv("GT_GROUNDTRUTH_WHEEL_HOST", str(gt_wheel))
    monkeypatch.setenv("GT_HARNESS_WHEEL_HOST", str(harness_wheel))
    monkeypatch.setenv("GT_HARNESS_WHEEL_SHA256", hashlib.sha256(b"harness").hexdigest())
    monkeypatch.setattr(
        "eval.miniswe_agent._groundtruth_release",
        lambda: {"wheel_sha256": hashlib.sha256(b"groundtruth").hexdigest()},
    )

    assert MiniSweAgent._gt_wheel() == gt_wheel
    assert MiniSweAgent._harness_wheel() == harness_wheel


@pytest.mark.asyncio
async def test_gt_run_binds_task_identity_into_index_evidence(monkeypatch, tmp_path):
    agent = MiniSweGtAgent(
        logs_dir=tmp_path / "logs",
        task_id="arktype-task",
        product_source_sha="a" * 40,
    )
    captured: dict[str, str] = {}

    monkeypatch.setattr(agent, "_model_and_env", lambda: ("model", {}))
    monkeypatch.setattr("eval.miniswe_agent.project_task_environment", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(agent, "resolve_env_vars", lambda: {})

    async def execute(_environment, _command, *, env, **_kwargs):
        captured.update(env)

    monkeypatch.setattr(agent, "exec_as_agent", execute)

    class Environment:
        async def agent_resource_snapshot(self):
            return {
                "schema": "gt.host_cgroup_snapshot.v1",
                "container_id_sha256": "b" * 64,
                "cgroup_path_sha256": "c" * 64,
                "oom": 0,
                "oom_kill": 0,
            }

    await agent.run.__wrapped__(agent, "task", Environment(), SimpleNamespace())

    assert captured["GT_TASK_ID"] == "arktype-task"
    assert captured["GT_PRODUCT_SOURCE_SHA"] == "a" * 40
    assert "GT_RESOURCE_ATTESTATION_KEY" not in captured


@pytest.mark.asyncio
async def test_gt_run_records_exact_resource_interval_around_runner(monkeypatch, tmp_path):
    agent = MiniSweGtAgent(
        logs_dir=tmp_path / "logs", task_id="arktype-task", product_source_sha="a" * 40
    )
    commands: list[str] = []
    snapshots = iter([(0, 0), (1, 1)])
    monkeypatch.setattr(agent, "_model_and_env", lambda: ("model", {}))
    monkeypatch.setattr("eval.miniswe_agent.project_task_environment", lambda *_a, **_k: {})
    monkeypatch.setattr(agent, "resolve_env_vars", lambda: {})

    async def execute(_environment, command, **_kwargs):
        commands.append(command)
        if "scripts.miniswe_supervisor" in command:
            raise NonZeroAgentExitCodeError("Command failed (exit 137)")

    class Environment:
        async def agent_resource_snapshot(self):
            oom, oom_kill = next(snapshots)
            return {
                "schema": "gt.host_cgroup_snapshot.v1",
                "container_id_sha256": "b" * 64,
                "cgroup_path_sha256": "c" * 64,
                "oom": oom,
                "oom_kill": oom_kill,
            }

    monkeypatch.setattr(agent, "exec_as_agent", execute)
    with pytest.raises(NonZeroAgentExitCodeError):
        await agent.run.__wrapped__(agent, "task", Environment(), SimpleNamespace())
    assert len(commands) == 1
    # Prefixed only by the staged language-server PATH; the installed runner
    # is still what executes.
    assert commands[0].startswith(f'PATH="{_REMOTE_LSP_BIN}:$PATH" exec ')
    assert "scripts.miniswe_supervisor" in commands[0]
    evidence = json.loads((tmp_path / "logs" / "agent-resource.json").read_text())
    assert evidence["attestation_scope"] == "host_agent_adapter"
    assert evidence["error_code"] == "GT_AGENT_CGROUP_OOM"


@pytest.mark.asyncio
async def test_gt_run_does_not_mislabel_other_nonzero_exit_as_137(monkeypatch, tmp_path):
    agent = MiniSweGtAgent(
        logs_dir=tmp_path / "logs", task_id="task-a", product_source_sha="a" * 40
    )
    monkeypatch.setattr(agent, "_model_and_env", lambda: ("model", {}))
    monkeypatch.setattr("eval.miniswe_agent.project_task_environment", lambda *_a, **_k: {})
    monkeypatch.setattr(agent, "resolve_env_vars", lambda: {})

    async def execute(*_args, **_kwargs):
        raise NonZeroAgentExitCodeError("Command failed (exit 1)")

    class Environment:
        snapshots = 0

        async def agent_resource_snapshot(self):
            self.snapshots += 1
            return {
                "schema": "gt.host_cgroup_snapshot.v1",
                "container_id_sha256": "b" * 64,
                "cgroup_path_sha256": "c" * 64,
                "oom": 0,
                "oom_kill": 0,
            }

    environment = Environment()
    monkeypatch.setattr(agent, "exec_as_agent", execute)
    with pytest.raises(NonZeroAgentExitCodeError):
        await agent.run.__wrapped__(agent, "task", environment, SimpleNamespace())

    assert environment.snapshots == 1
    assert not (Path(agent.logs_dir) / "agent-resource.json").exists()


@pytest.mark.asyncio
async def test_gt_run_refuses_invalid_identity_before_any_command(monkeypatch, tmp_path):
    agent = MiniSweGtAgent(logs_dir=tmp_path / "logs", task_id="", product_source_sha="a" * 40)
    calls = 0
    monkeypatch.setattr(agent, "_model_and_env", lambda: ("model", {}))
    monkeypatch.setattr("eval.miniswe_agent.project_task_environment", lambda *_a, **_k: {})
    monkeypatch.setattr(agent, "resolve_env_vars", lambda: {})

    async def execute(*_args, **_kwargs):
        nonlocal calls
        calls += 1

    monkeypatch.setattr(agent, "exec_as_agent", execute)
    with pytest.raises(ValueError, match="identity is incomplete"):
        await agent.run.__wrapped__(agent, "task", SimpleNamespace(), SimpleNamespace())
    assert calls == 0


@pytest.mark.asyncio
async def test_failed_host_finalization_removes_task_forged_resource(monkeypatch, tmp_path):
    agent = MiniSweGtAgent(
        logs_dir=tmp_path / "logs", task_id="task-a", product_source_sha="a" * 40
    )
    monkeypatch.setattr(agent, "_model_and_env", lambda: ("model", {}))
    monkeypatch.setattr("eval.miniswe_agent.project_task_environment", lambda *_a, **_k: {})
    monkeypatch.setattr(agent, "resolve_env_vars", lambda: {})
    calls = 0

    async def execute(_environment, command, **_kwargs):
        nonlocal calls
        calls += 1
        if "scripts.miniswe_supervisor" in command:
            Path(agent.logs_dir).mkdir(parents=True, exist_ok=True)
            (Path(agent.logs_dir) / "agent-resource.json").write_text("forged")
            raise NonZeroAgentExitCodeError("Command failed (exit 137)")
        return None

    class Environment:
        snapshots = 0

        async def agent_resource_snapshot(self):
            self.snapshots += 1
            if self.snapshots > 1:
                raise RuntimeError("host snapshot failed")
            return {
                "schema": "gt.host_cgroup_snapshot.v1",
                "container_id_sha256": "b" * 64,
                "cgroup_path_sha256": "c" * 64,
                "oom": 0,
                "oom_kill": 0,
            }

    monkeypatch.setattr(agent, "exec_as_agent", execute)
    with pytest.raises(NonZeroAgentExitCodeError):
        await agent.run.__wrapped__(agent, "task", Environment(), SimpleNamespace())
    assert not (Path(agent.logs_dir) / "agent-resource.json").exists()


@pytest.mark.asyncio
async def test_installer_uses_canonical_version_and_verified_uv_installer(monkeypatch, tmp_path):
    wheel = tmp_path / "groundtruth_mcp-1.0.0-py3-none-any.whl"
    harness_wheel = tmp_path / "nano_harness-0.0.1-py3-none-any.whl"
    binary = tmp_path / "gt-index-linux-amd64"
    uv_installer = tmp_path / "uv-install.sh"
    python_archive = tmp_path / "python-3.12.13.tar.gz"
    wheelhouse = tmp_path / "wheelhouse"
    wheel.write_bytes(b"wheel")
    harness_wheel.write_bytes(b"harness-wheel")
    binary.write_bytes(b"binary")
    uv_installer.write_bytes(b"installer")
    python_archive.write_bytes(b"python-archive")
    wheelhouse.mkdir()
    (wheelhouse / "dependency.whl").write_bytes(b"wheel")
    monkeypatch.delenv("MINISWE_AGENT_VERSION", raising=False)
    monkeypatch.setattr(MiniSweAgent, "_gt_wheel", staticmethod(lambda: wheel))
    monkeypatch.setattr(MiniSweAgent, "_harness_wheel", staticmethod(lambda: harness_wheel))
    monkeypatch.setattr(MiniSweAgent, "_gt_binary_host", staticmethod(lambda: binary))
    monkeypatch.setattr(MiniSweAgent, "_uv_installer_host", staticmethod(lambda: uv_installer))
    monkeypatch.setattr(MiniSweAgent, "_python_archive_host", staticmethod(lambda: python_archive))
    monkeypatch.setattr(MiniSweAgent, "_wheelhouse_host", staticmethod(lambda: wheelhouse))

    commands: list[str] = []

    class Environment:
        async def upload_dir(self, source, destination):
            pass

        async def upload_file(self, source, destination):
            pass

    async def exec_as_root(environment, command, **kwargs):
        commands.append(command)

    async def exec_as_agent(environment, command, **kwargs):
        commands.append(command)

    agent = MiniSweAgent(logs_dir=tmp_path / "logs")
    monkeypatch.setattr(agent, "exec_as_root", exec_as_root)
    monkeypatch.setattr(agent, "exec_as_agent", exec_as_agent)

    await agent.install(Environment())

    install = next(command for command in commands if "tool install --offline" in command)
    assert '--with "mini-swe-agent==2.4.6"' in install
    assert "m.version('mini-swe-agent') == '2.4.6'" in install
    assert "sha256sum -c -" in install
    assert "--with 'onnxruntime==1.20.1'" in install
    assert "--with 'tokenizers==0.23.1'" in install
    assert "m.version('onnxruntime') == '1.20.1'" in install
    assert "m.version('tokenizers') == '0.23.1'" in install
    assert "onnxruntime, tokenizers" in install
    assert "curl -LsSf" not in install
    assert str(harness_wheel.name) in install
    assert "/installed-agent/miniswe" not in install
