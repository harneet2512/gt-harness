from __future__ import annotations

import sys

from minisweagent.agents.default import DefaultAgent
from minisweagent.environments.local import LocalEnvironment
from minisweagent.models.litellm_model import BASH_TOOL, LitellmModel

from gt_engine.gt_session import GTSession, GTSessionConfig
from gt_engine.miniswe_integration import MiniSweAdapter
from gt_engine.miniswe_runtime import install_runtime_hooks
from gt_engine.miniswe_typed_actions import GroundTruthLitellmModel


def configured(tmp_path, monkeypatch):
    model = GroundTruthLitellmModel(model_name="openai/test", cost_tracking="ignore_errors")
    agent = DefaultAgent(model, LocalEnvironment(cwd=str(tmp_path)),
                         system_template="system", instance_template="{{task}}")
    adapter = MiniSweAdapter(task_id="test", state_dir=tmp_path / "state", predicates=[])
    # Isolate optional receipt hooks from the actual native transport/action seam.
    monkeypatch.setattr(adapter, "attach_provider_boundary", lambda *_: None)
    session = GTSession(GTSessionConfig(task_id="test", state_dir=str(tmp_path / "state")),
                        engine=adapter)
    install_runtime_hooks(agent, session)
    return agent, adapter, session


def test_degraded_transport_is_native_and_does_not_require_gt_metadata(tmp_path, monkeypatch):
    import gt_engine.miniswe_typed_actions as typed

    agent, _, session = configured(tmp_path, monkeypatch)
    monkeypatch.delenv("GT_PROVIDER_CONTEXT_WINDOW_TOKENS", raising=False)
    calls = []
    monkeypatch.setattr(typed.litellm, "completion", lambda **kwargs: calls.append(kwargs) or "native")
    session.degrade("injected", OSError("failed optional observer"))
    assert agent.model._query([{"role": "user", "content": "native request"}]) == "native"
    assert calls[0]["tools"] == [BASH_TOOL]
    assert calls[0]["messages"] == [{"role": "user", "content": "native request"}]


def test_failed_preparation_returns_unmodified_native_content(tmp_path, monkeypatch):
    agent, _, session = configured(tmp_path, monkeypatch)

    def fail(*_args, **_kwargs):
        raise OSError("cannot prepare")

    monkeypatch.setattr(session, "before_model", fail)
    messages = [{"role": "user", "content": "native task"}]
    prepared = agent.model._prepare_messages_for_api(messages)
    assert prepared == messages
    assert session.disabled


def test_real_native_action_batch_survives_optional_gt_failure(tmp_path, monkeypatch):
    agent, _, session = configured(tmp_path, monkeypatch)
    native = DefaultAgent(LitellmModel(model_name="openai/test", cost_tracking="ignore_errors"),
                          LocalEnvironment(cwd=str(tmp_path)),
                          system_template="system", instance_template="{{task}}")

    def fail(**_kwargs):
        raise OSError("optional after-action failure")

    monkeypatch.setattr(session, "after_action", fail)
    actions = [{"command": f'"{sys.executable}" -c "print(\'native-{i}\')"',
                "tool_call_id": f"call-{i}", "tool_name": "bash"} for i in range(2)]
    message = {"role": "assistant", "content": "", "extra": {"actions": actions}}
    expected = native.execute_actions(message)
    actual = agent.execute_actions(message)
    assert [(row["role"], row["tool_call_id"], row["content"]) for row in actual] == [
        (row["role"], row["tool_call_id"], row["content"]) for row in expected
    ]
    assert session.disabled
