from __future__ import annotations

import pytest

from gt_engine.gt_session import GTMode, GTSession, GTSessionConfig
from gt_engine.miniswe_controller import Predicate
from gt_engine.miniswe_integration import MiniSweAdapter
from gt_engine.miniswe_runtime import install_runtime_hooks


class PassThroughModel:
    def _prepare_messages_for_api(self, messages):
        return list(messages)

    def query(self, messages, **kwargs):
        return {"role": "assistant", "content": "", "extra": {"actions": []}}

    def format_observation_messages(self, message, outputs, template_vars=None):
        return [
            {
                "role": "tool",
                "content": out["output"],
                "tool_call_id": f"c{index}",
            }
            for index, out in enumerate(outputs)
        ]


class RecordingEnvironment:
    def __init__(self):
        self.executed: list[str] = []

    def execute(self, action):
        command = action.get("command") or action.get("cmd") or ""
        self.executed.append(command)
        return {"output": f"ORIGINAL::{command}", "returncode": 0}


class PassThroughAgent:
    def __init__(self):
        self.model = PassThroughModel()
        self.env = RecordingEnvironment()
        self.messages = []

    def execute_actions(self, message):
        return []

    def add_messages(self, *messages):
        self.messages.extend(messages)
        return list(messages)

    def get_template_vars(self):
        return {}


def _advisory_agent(tmp_path):
    agent = PassThroughAgent()
    adapter = MiniSweAdapter(
        task_id="capability", state_dir=tmp_path,
        predicates=[Predicate("p", "unrelated")],
    )
    session = GTSession(
        GTSessionConfig(
            task_id=adapter.task_id,
            state_dir=str(tmp_path),
            mode=GTMode.ADVISORY,
        ),
        engine=adapter,
    )
    install_runtime_hooks(agent, session)
    return agent, adapter, session


@pytest.mark.parametrize(
    ("capability", "command"),
    [
        ("CAP-BASH-001", "printf unrestricted-bash"),
        ("CAP-SEARCH-002", "python custom_search.py --any-strategy"),
        ("CAP-READ-003", "cat completely-unranked-file.txt"),
        ("CAP-EDIT-004", "python edit_any_permissible_file.py"),
        ("CAP-CREATE-005", "touch newly-created-file.txt"),
        ("CAP-DELETE-006", "rm obsolete-permissible-file.txt"),
        ("CAP-HYPOTHESIS-007", "python probe_model_owned_hypothesis.py"),
        ("CAP-TEST-008", "python bespoke_verifier.py --expensive"),
        ("CAP-OUTSIDE-009", "cat path/outside/gt/localization.txt"),
        ("CAP-STRATEGY-010", "python abandon_first_strategy.py"),
    ],
)
def test_advisory_gt_executes_every_baseline_action_unchanged(
    capability, command, tmp_path
):
    agent, _adapter, _session = _advisory_agent(tmp_path)
    messages = agent.execute_actions({
        "extra": {"actions": [{"command": command, "tool_call_id": capability}]}
    })
    assert agent.env.executed == [command], capability
    assert any(f"ORIGINAL::{command}" in row["content"] for row in messages), capability


def test_cap_submit_011_unknown_and_red_are_nonblocking_in_default_mode(tmp_path):
    for status in (None, "RED"):
        agent, adapter, _session = _advisory_agent(tmp_path / str(status))
        if status:
            adapter.record_receipt(
                "p", "failing check", 1, "failure", epoch=0,
                status=status, semantic=True,
            )
        command = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
        agent.execute_actions({
            "extra": {"actions": [{"command": command, "tool_call_id": "submit"}]}
        })
        assert agent.env.executed == [command]


def test_cap_malformed_014_empty_command_still_reaches_baseline_environment(tmp_path):
    agent, _adapter, _session = _advisory_agent(tmp_path)
    messages = agent.execute_actions({
        "extra": {"actions": [{"command": "", "tool_call_id": "empty"}]}
    })
    assert agent.env.executed == [""]
    assert any("ORIGINAL::" in row["content"] for row in messages)


def test_cap_failopen_012_gt_fault_does_not_consume_current_or_future_action(
    monkeypatch, tmp_path
):
    agent, adapter, session = _advisory_agent(tmp_path)

    def fail_once(*args, **kwargs):
        raise RuntimeError("GT unavailable")

    monkeypatch.setattr(adapter, "before_action", fail_once)
    first = "printf first"
    second = "printf second"
    agent.execute_actions({"extra": {"actions": [
        {"command": first, "tool_call_id": "c1"},
        {"command": second, "tool_call_id": "c2"},
    ]}})
    assert session.disabled is True
    assert agent.env.executed == [first, second]


def test_cap_bypass_013_restores_original_miniswe_methods(tmp_path):
    agent = PassThroughAgent()
    original_execute = agent.execute_actions
    adapter = MiniSweAdapter(task_id="capability", state_dir=tmp_path, predicates=[])
    session = GTSession(GTSessionConfig(task_id="capability"), engine=adapter)
    handle = install_runtime_hooks(agent, session)
    handle.restore()
    assert agent.execute_actions == original_execute
