from eval.tb_agent import _GT_STAGED_SOURCE_CLEANUP, GTNanoAgent


def test_gt_adapter_version_does_not_require_staged_checkout():
    command = GTNanoAgent.get_version_command(GTNanoAgent)

    assert command is not None
    assert "/installed-agent/nano-harness" not in command
    assert "$HOME/.local/share/uv/tools/nano-harness/bin/python" in command


def test_gt_staged_source_cleanup_is_exact_and_guarded():
    assert _GT_STAGED_SOURCE_CLEANUP == (
        "test \"$(readlink -f /installed-agent/nano-harness)\" = "
        "\"/installed-agent/nano-harness\" && "
        "rm -rf -- /installed-agent/nano-harness"
    )


def test_gt_run_uses_external_private_state_directory(monkeypatch, tmp_path):
    class _Environment:
        pass

    captured = {}

    async def fake_exec(_self, _environment, command, env):
        captured["command"] = command
        captured["env"] = env

    monkeypatch.setattr(GTNanoAgent, "exec_as_agent", fake_exec)
    agent = GTNanoAgent(logs_dir=tmp_path)
    agent.model_name = "deepseek-v4-flash"
    agent.gt_profile = "2"

    import asyncio

    asyncio.run(agent.run("task", _Environment(), object()))

    assert captured["env"]["GT_STATE_DIR"] == "/tmp/.nano-gt-state"
    assert '--gt-root "$PWD"' in captured["command"]
