from __future__ import annotations

import pytest

from eval.miniswe_agent import (
    _DEFAULT_MINISWE_AGENT_VERSION,
    _PYTHON_VERSION,
    _UV_INSTALL,
    _UV_VERSION,
    MiniSweAgent,
    MiniSweGtAgent,
    _miniswe_agent_version,
)


def test_gt_off_and_gt_on_share_the_exact_installer_implementation():
    assert MiniSweGtAgent.install is MiniSweAgent.install


def test_installer_runtime_versions_are_exact_not_floating():
    assert _UV_INSTALL == f"https://astral.sh/uv/{_UV_VERSION}/install.sh"
    assert _UV_VERSION == "0.11.32"
    assert _PYTHON_VERSION == "3.12.13"


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
    agent = MiniSweAgent(logs_dir=tmp_path / "logs", max_iterations=300)

    command = agent._run_command("task", "deepseek-v4-flash")

    assert "--step-limit 300" in command


@pytest.mark.asyncio
async def test_installer_uses_canonical_version_and_verified_uv_installer(
    monkeypatch, tmp_path
):
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
    monkeypatch.setattr(
        MiniSweAgent, "_harness_wheel", staticmethod(lambda _output: harness_wheel)
    )
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

    install = commands[-1]
    assert '--with "mini-swe-agent==2.4.6"' in install
    assert "m.version('mini-swe-agent') == '2.4.6'" in install
    assert "sha256sum -c -" in install
    assert "curl -LsSf" not in install
    assert str(harness_wheel.name) in install
    assert "/installed-agent/miniswe" not in install
