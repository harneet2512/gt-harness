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


def test_miniswe_agent_version_allows_current_release(monkeypatch):
    monkeypatch.setenv("MINISWE_AGENT_VERSION", "2.4.6")

    assert _miniswe_agent_version() == "2.4.6"


def test_miniswe_agent_version_rejects_every_other_value(monkeypatch):
    monkeypatch.setenv("MINISWE_AGENT_VERSION", "2.2.9")

    try:
        _miniswe_agent_version()
    except ValueError as exc:
        assert "MINISWE_AGENT_VERSION" in str(exc)
        assert "2.4.6" in str(exc)
    else:
        raise AssertionError("unsupported Mini-SWE version was accepted")


@pytest.mark.asyncio
async def test_installer_uses_historical_version_in_install_and_assertion(
    monkeypatch, tmp_path
):
    binary = tmp_path / "gt-index"
    binary.write_bytes(b"binary")
    monkeypatch.setenv("MINISWE_AGENT_VERSION", "2.4.6")
    monkeypatch.setattr(MiniSweAgent, "_gt_binary_host", staticmethod(lambda: binary))

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
    assert "m.version('gt-harness') == '0.9.0'" in install
    assert "groundtruth_mcp" not in install
